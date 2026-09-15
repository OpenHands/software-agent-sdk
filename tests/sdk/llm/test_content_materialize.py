"""Tests for the ``BaseContent.materialize()`` contract.

Covers: data-URL decode -> file, http-URL download -> file (with size cap),
a ``TextContent`` no-op, and content-addressed idempotency (no duplicate
writes across repeated materialization).
"""

import base64
from pathlib import Path

import httpx
import pytest

from openhands.sdk.llm import ImageContent, MaterializedRef, TextContent
from openhands.sdk.llm.utils import content_materialize
from openhands.sdk.llm.utils.content_materialize import (
    download_url,
    parse_data_url,
)
from openhands.sdk.workspace import LocalWorkspace


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg=="
)


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def test_parse_data_url_decodes_bytes_and_mime() -> None:
    data, mime = parse_data_url(_data_url(PNG_BYTES))
    assert data == PNG_BYTES
    assert mime == "image/png"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x.png",  # not a data URL
        "data:image/png,notbase64",  # missing ;base64
        "data:image/png;base64",  # missing comma
        "data:image/png;base64,!!!notbase64!!!",  # invalid payload
    ],
)
def test_parse_data_url_rejects_malformed(url: str) -> None:
    with pytest.raises(ValueError):
        parse_data_url(url)


def test_text_content_materialize_is_noop(tmp_path: Path) -> None:
    ws = LocalWorkspace(working_dir=str(tmp_path))
    assert TextContent(text="hello").materialize(ws) == []


def test_image_content_materialize_data_url_writes_file(tmp_path: Path) -> None:
    ws = LocalWorkspace(working_dir=str(tmp_path))
    content = ImageContent(image_urls=[_data_url(PNG_BYTES)])

    refs = content.materialize(ws)

    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, MaterializedRef)
    written = Path(ref.path)
    assert written.is_file()
    assert written.read_bytes() == PNG_BYTES
    assert ref.mime_type == "image/png"
    assert ref.size_bytes == len(PNG_BYTES)
    # data: URLs are not echoed back as a source URL.
    assert ref.source_url is None
    # Written under the workspace working dir.
    assert str(tmp_path) in ref.path


def test_image_content_materialize_http_url_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.com/pic.png"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(
            200, content=PNG_BYTES, headers={"content-type": "image/png"}
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(content_materialize.httpx, "Client", fake_client)

    ws = LocalWorkspace(working_dir=str(tmp_path))
    refs = ImageContent(image_urls=[url]).materialize(ws)

    assert len(refs) == 1
    assert Path(refs[0].path).read_bytes() == PNG_BYTES
    assert refs[0].source_url == url


def test_download_url_enforces_size_cap_via_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"x" * 100, headers={"content-length": "100"}
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(content_materialize.httpx, "Client", fake_client)

    with pytest.raises(ValueError, match="exceeds cap"):
        download_url("https://example.com/big.bin", max_bytes=10)


def test_download_url_enforces_size_cap_while_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An understated content-length must not let the real payload bypass the
    # cap: the byte counter enforces it while streaming.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100, headers={"content-length": "5"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(content_materialize.httpx, "Client", fake_client)

    with pytest.raises(ValueError, match="exceeded cap"):
        download_url("https://example.com/big.bin", max_bytes=10)


def test_materialize_is_idempotent_no_duplicate_writes(tmp_path: Path) -> None:
    ws = LocalWorkspace(working_dir=str(tmp_path))
    content = ImageContent(image_urls=[_data_url(PNG_BYTES)])

    first = content.materialize(ws)
    second = content.materialize(ws)

    # Content-addressed: both resolve to the same path.
    assert first[0].path == second[0].path
    # Exactly one file exists in the materialized subdir.
    subdir = tmp_path / content_materialize.MATERIALIZE_SUBDIR
    files = list(subdir.iterdir())
    assert len(files) == 1


def test_image_content_materialize_skips_unsupported_scheme(tmp_path: Path) -> None:
    ws = LocalWorkspace(working_dir=str(tmp_path))
    refs = ImageContent(image_urls=["ftp://example.com/x.png"]).materialize(ws)
    assert refs == []
