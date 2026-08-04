import json as jsonlib
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
from pydantic import SecretStr

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.secret import StaticSecret
from openhands.tools.pyromind_dataset.definition import (
    _PREVIEW_DATASET_DESCRIPTION,
    PYROMIND_STORAGE_AUTH_COOKIE_SECRET,
    PYROMIND_STORAGE_HEADERS_STATE_KEY,
    PreviewDatasetAction,
    PreviewDatasetExecutor,
    UploadFileToPyromindAction,
    UploadFileToPyromindExecutor,
    _match_shared_dataset,
    _resolve_workspace_dir,
    download_file_from_pyromind,
)


def test_preview_description_mentions_shared_and_storage() -> None:
    assert "shared" in _PREVIEW_DATASET_DESCRIPTION.lower()
    assert "storage" in _PREVIEW_DATASET_DESCRIPTION.lower()
    assert "openai/gsm8k" in _PREVIEW_DATASET_DESCRIPTION
    assert "auto-selects" in _PREVIEW_DATASET_DESCRIPTION


def test_match_shared_dataset_exact() -> None:
    datasets = ["openai/gsm8k", "pyromind/self-cognition"]
    assert _match_shared_dataset("openai/gsm8k", datasets) == ("openai/gsm8k", "")
    assert _match_shared_dataset("openai/gsm8k/", datasets) == ("openai/gsm8k", "")


def test_match_shared_dataset_with_file_path() -> None:
    datasets = ["openai/gsm8k", "pyromind/self-cognition"]
    result = _match_shared_dataset("openai/gsm8k/data/train.jsonl", datasets)
    assert result == ("openai/gsm8k", "data/train.jsonl")


def test_match_shared_dataset_no_match() -> None:
    datasets = ["openai/gsm8k", "pyromind/self-cognition"]
    assert _match_shared_dataset("datasets/my_data/train.jsonl", datasets) is None
    assert _match_shared_dataset("/start-hook.sh", datasets) is None


def test_match_shared_dataset_longest_prefix() -> None:
    datasets = ["org/data", "org/data-v2"]
    result = _match_shared_dataset("org/data-v2/file.jsonl", datasets)
    assert result == ("org/data-v2", "file.jsonl")


class _FakeWorkspace:
    def __init__(self, working_dir: Path) -> None:
        self.working_dir = str(working_dir)


class _Response:
    def __init__(self, status_code: int, payload: Any, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _StreamResponse:
    def __init__(self, content: bytes, *, status_code: int = 200) -> None:
        self._content = content
        self.status_code = status_code
        self.headers = {"content-length": str(len(content))}

    def __enter__(self) -> "_StreamResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def iter_bytes(self):
        yield self._content

    def read(self) -> bytes:
        return self._content


_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000456")


def _fake_conversation(
    tmp_path: Path,
    *,
    secret_registry: SecretRegistry | None = None,
    agent_state: dict[str, Any] | None = None,
):
    return type(
        "FakeConversation",
        (),
        {
            "id": _CONVERSATION_ID,
            "workspace": _FakeWorkspace(tmp_path),
            "state": type(
                "FakeState",
                (),
                {
                    "secret_registry": secret_registry or SecretRegistry(),
                    "agent_state": agent_state or {},
                },
            )(),
        },
    )()


def _patch_shared_empty(monkeypatch) -> None:
    """Patch httpx.get so shared dataset lookup returns no match (fallback)."""

    def fake_get(url, *, headers, params=None, timeout):
        return _Response(200, {"success": True, "data": {"datasets": [], "total": 0}})

    monkeypatch.setattr(httpx, "get", fake_get)


def _secret_registry() -> SecretRegistry:
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            PYROMIND_STORAGE_AUTH_COOKIE_SECRET: StaticSecret(
                value=SecretStr("auth_token=session-token")
            )
        }
    )
    return secret_registry


def test_preview_dataset_reads_jsonl_samples_with_storage_context(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)
    calls: list[dict[str, Any]] = []
    jsonl = b'{"prompt":"p1","completion":"c1"}\n{"prompt":"p2","completion":"c2"}\n'

    def fake_post(url, *, headers, json, timeout):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "datasets/train.jsonl",
                        "bucket_name": "1001",
                        "size": len(jsonl),
                        "content_type": "application/jsonl",
                        "is_dir": False,
                        "metadata": {},
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/train"}},
            )
        raise AssertionError(f"unexpected URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        assert method == "GET"
        assert url == "https://download.test/train"
        assert headers["range"] == "bytes=0-10239"
        assert timeout == 5.0
        assert follow_redirects is True
        return _StreamResponse(jsonl)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(
        tmp_path,
        secret_registry=_secret_registry(),
        agent_state={PYROMIND_STORAGE_HEADERS_STATE_KEY: {"x-cluster": "pre"}},
    )

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
        timeout=5.0,
    )(
        PreviewDatasetAction.model_validate(
            {"dataset_path": "datasets/train.jsonl", "max_samples": 1}
        ),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.num_rows == 2
    assert observation.previewed_rows == 2
    assert observation.preview_truncated is False
    assert "prompt" in observation.columns
    assert "completion" in observation.columns
    assert len(observation.sample_rows) == 1
    row = observation.sample_rows[0]
    assert row["line"] == 1
    assert row["text"] == '{"prompt":"p1","completion":"c1"}'
    assert row["prompt"] == "p1"
    assert row["completion"] == "c1"
    assert "sample_file_path" not in observation.text
    assert calls[0]["headers"]["cookie"] == "auth_token=session-token"
    assert calls[0]["headers"]["x-cluster"] == "pre"


def test_preview_dataset_formats_text_file_content(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)
    lines = [f"echo line-{index}" for index in range(1, 19)]
    content = ("\n".join(lines) + "\n").encode()

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "start-hook.sh",
                        "size": len(content),
                        "content_type": "",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/hook"}},
            )
        raise AssertionError(f"unexpected URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        assert method == "GET"
        assert url == "https://download.test/hook"
        return _StreamResponse(content)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="/start-hook.sh", n=20),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.num_rows == 18
    assert observation.previewed_rows == 18
    assert len(observation.sample_rows) == 18
    assert "sample_file_path" not in observation.text


def test_preview_dataset_defaults_to_ten_sample_rows(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)
    lines = [f"line-{index}" for index in range(1, 19)]
    content = ("\n".join(lines) + "\n").encode()

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "small.txt",
                        "size": len(content),
                        "content_type": "text/plain",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/small"}},
            )
        raise AssertionError(f"unexpected URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        return _StreamResponse(content)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)
    (tmp_path / "preview_dataset").mkdir()

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(PreviewDatasetAction(dataset_path="/small.txt"), cast(Any, conversation))

    assert not observation.is_error
    assert observation.num_rows == 18
    assert len(observation.sample_rows) == 10
    assert "rows=18" in observation.text
    assert "sample_rows=10" in observation.text


def test_preview_dataset_large_file_uses_random_ranges(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)
    lines = [f"line-{index:04d} {'x' * 120}" for index in range(1, 500)]
    content = ("\n".join(lines) + "\n").encode()
    ranges: list[tuple[int, int]] = []

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "large.txt",
                        "size": len(content),
                        "content_type": "text/plain",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/large"}},
            )
        raise AssertionError(f"unexpected URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        raw_range = headers["range"].removeprefix("bytes=")
        start, end = [int(part) for part in raw_range.split("-", maxsplit=1)]
        ranges.append((start, end))
        return _StreamResponse(content[start : end + 1])

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="/large.txt", n=8),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.preview_truncated is True
    assert len(ranges) > 1
    assert ranges[0][0] == 0
    assert any(start > 0 for start, _ in ranges)
    assert 0 < len(observation.sample_rows) <= 8
    assert "sample_integrity=partial_byte_fragments" in observation.text
    assert "use them only as a format hint" in observation.text


def test_preview_dataset_empty_file_does_not_request_download_url(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        assert url.endswith("/get_file_metadata")
        return _Response(
            200,
            {
                "success": True,
                "data": {
                    "object_name": "errors.jsonl",
                    "size": 0,
                    "content_type": "application/jsonl",
                    "is_dir": False,
                },
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="/run/errors.jsonl", n=3),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.num_rows == 0
    assert observation.previewed_rows == 0
    assert observation.preview_truncated is False
    assert observation.sample_rows == []


def test_preview_dataset_truncates_large_jsonl_and_reduces_samples(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)
    rows = [f'{{"i":{i}}}\n'.encode() for i in range(3000)]
    content = b"".join(rows)
    assert len(content) > 10 * 1024

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "big.jsonl",
                        "size": len(content),
                        "content_type": "application/jsonl",
                        "is_dir": False,
                    },
                },
            )
        return _Response(200, {"success": True, "data": {"url": "https://download"}})

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        raw_range = headers["range"].removeprefix("bytes=")
        start, end = [int(p) for p in raw_range.split("-", maxsplit=1)]
        return _StreamResponse(content[start : end + 1])

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
        max_preview_bytes=len(rows[0]) * 5 + 2,
    )(
        PreviewDatasetAction.model_validate(
            {"dataset_path": "big.jsonl", "max_samples": 10}
        ),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.preview_truncated is True
    assert observation.num_rows is None
    assert observation.previewed_rows is not None
    assert observation.previewed_rows > 0
    assert 0 < len(observation.sample_rows) <= 10


def test_upload_file_to_pyromind_posts_workspace_file(
    monkeypatch,
    tmp_path,
):
    local_file = tmp_path / "metric.py"
    local_file.write_text("def acc():\n    return 1\n", encoding="utf-8")
    calls: dict[str, Any] = {}

    def fake_post(url, *, headers, data, files, timeout):
        uploaded_file = files["file"]
        calls.update(
            {
                "url": url,
                "headers": headers,
                "data": data,
                "filename": uploaded_file[0],
                "content": uploaded_file[1].read(),
                "timeout": timeout,
            }
        )
        return _Response(
            200,
            {
                "success": True,
                "data": {
                    "uploaded": True,
                    "success_count": 1,
                    "failed_count": 0,
                    "success_files": [{"filename": "metric.py"}],
                    "failed_files": [],
                },
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    conversation = _fake_conversation(
        tmp_path,
        secret_registry=_secret_registry(),
        agent_state={PYROMIND_STORAGE_HEADERS_STATE_KEY: {"x-cluster": "pre"}},
    )

    observation = UploadFileToPyromindExecutor(
        storage_base_url="https://portal.test/storage_api",
        timeout=7.0,
    )(
        UploadFileToPyromindAction(file_path="metric.py"),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.storage_path == (
        f"/.pyromind-agent/{_CONVERSATION_ID}/metric.py"
    )
    assert calls["url"] == "https://portal.test/storage_api/upload_file"
    assert calls["headers"]["cookie"] == "auth_token=session-token"
    assert calls["headers"]["x-cluster"] == "pre"
    assert calls["data"]["path"] == f"/.pyromind-agent/{_CONVERSATION_ID}"
    assert calls["filename"] == "metric.py"
    assert calls["content"] == b"def acc():\n    return 1\n"
    assert calls["timeout"] == 7.0


def test_upload_file_to_pyromind_explicit_target_dir_wins(
    monkeypatch,
    tmp_path,
):
    local_file = tmp_path / "metric.py"
    local_file.write_text("def acc():\n    return 1\n", encoding="utf-8")
    posted_path: dict[str, str] = {}

    def fake_post(url, *, headers, data, files, timeout):
        posted_path["path"] = data["path"]
        return _Response(
            200,
            {
                "success": True,
                "data": {
                    "uploaded": True,
                    "success_count": 1,
                    "failed_count": 0,
                    "success_files": [{"filename": "metric.py"}],
                    "failed_files": [],
                },
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    conversation = _fake_conversation(tmp_path, secret_registry=_secret_registry())

    observation = UploadFileToPyromindExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        UploadFileToPyromindAction(
            file_path="metric.py",
            target_dir="/custom/dir",
        ),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.storage_path == "/custom/dir/metric.py"
    assert posted_path["path"] == "/custom/dir"


def test_download_file_from_pyromind_returns_bounded_script(monkeypatch):
    def fake_post(url, *, headers, json, timeout):
        assert json == {"path": "/agentTest/clean.py"}
        return _Response(
            200,
            {"success": True, "data": {"url": "https://download.test/script"}},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *args, **kwargs: _StreamResponse(b"def main():\n    return 0\n"),
    )

    content = download_file_from_pyromind(
        storage_path="/agentTest/clean.py",
        storage_base_url="https://portal.test/storage_api",
        headers={"cookie": "session"},
        timeout=3,
        max_bytes=1024,
    )

    assert content == b"def main():\n    return 0\n"


def test_upload_file_to_pyromind_rejects_workspace_escape(monkeypatch, tmp_path):
    def fake_post(url, *, headers, data, files, timeout):
        raise AssertionError("upload API should not be called")

    monkeypatch.setattr(httpx, "post", fake_post)
    outside = tmp_path.parent / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    conversation = _fake_conversation(tmp_path)

    observation = UploadFileToPyromindExecutor(
        storage_base_url="https://portal.test/storage_api"
    )(
        UploadFileToPyromindAction(file_path=str(outside)),
        cast(Any, conversation),
    )

    assert observation.is_error
    assert "outside the conversation workspace" in observation.text


def test_preview_dataset_reports_invalid_json(monkeypatch):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        return _Response(200, jsonlib.JSONDecodeError("bad json", doc="{", pos=0))

    monkeypatch.setattr(httpx, "post", fake_post)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api"
    )(PreviewDatasetAction(dataset_path="data.jsonl"))

    assert observation.is_error
    assert "invalid JSON" in observation.text


def test_preview_dataset_single_line_jsonl_returns_partial_content(
    monkeypatch,
    tmp_path,
):
    """A single-line JSONL file larger than 20KB should still
    return a truncated preview instead of zero rows."""
    _patch_shared_empty(monkeypatch)
    big_value = "x" * 25000
    single_line = f'{{"prompt":"hello","value":"{big_value}"}}'
    content = single_line.encode("utf-8")
    assert len(content) > 10 * 1024

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "single.jsonl",
                        "size": len(content),
                        "content_type": "application/jsonl",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/single"}},
            )
        raise AssertionError(f"unexpected URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        raw_range = headers["range"].removeprefix("bytes=")
        start, end = [int(p) for p in raw_range.split("-", maxsplit=1)]
        return _StreamResponse(content[start : end + 1])

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="single.jsonl", n=5),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.preview_truncated is True
    assert observation.previewed_rows is not None
    assert observation.previewed_rows > 0
    assert len(observation.sample_rows) > 0
    assert "text" in observation.sample_rows[0]


def test_preview_dataset_single_line_text_returns_partial_content(
    monkeypatch,
    tmp_path,
):
    """A single-line text file larger than 20KB should still
    return a truncated preview instead of zero rows."""
    _patch_shared_empty(monkeypatch)
    content = b"a" * 25000
    assert len(content) > 10 * 1024

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "single.txt",
                        "size": len(content),
                        "content_type": "text/plain",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/single"}},
            )
        raise AssertionError(f"unexpected URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        raw_range = headers["range"].removeprefix("bytes=")
        start, end = [int(p) for p in raw_range.split("-", maxsplit=1)]
        return _StreamResponse(content[start : end + 1])

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="single.txt", n=5),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.preview_truncated is True
    assert observation.previewed_rows is not None
    assert observation.previewed_rows > 0
    assert len(observation.sample_rows) > 0
    assert observation.sample_rows[0]["text"].startswith("a")


def test_preview_dataset_marks_json_wrapped_text_with_format_hint(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)
    content = jsonlib.dumps(
        {"rows": [{"row_idx": 0, "row": {"text": "x" * 25000}}]}
    ).encode()

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "wrapped.txt",
                        "size": len(content),
                        "content_type": "text/plain",
                        "is_dir": False,
                    },
                },
            )
        return _Response(
            200,
            {"success": True, "data": {"url": "https://download.test/wrapped"}},
        )

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        raw_range = headers["range"].removeprefix("bytes=")
        start, end = [int(part) for part in raw_range.split("-", maxsplit=1)]
        return _StreamResponse(content[start : end + 1])

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="wrapped.txt", n=3),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert "format_hint=json-like" in observation.text
    assert observation.sample_rows[0]["text"].startswith('{"rows"')


# ---------------------------------------------------------------------------
# Shared dataset space tests
# ---------------------------------------------------------------------------


def test_shared_preview_with_specific_file(monkeypatch, tmp_path):
    """Preview a specific file in a shared dataset."""
    preview_lines = [
        '{"prompt":"p1","completion":"c1"}',
        '{"prompt":"p2","completion":"c2"}',
    ]

    def fake_get(url, *, headers, params=None, timeout):
        if "/datasets/preview" in url:
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "dataset": "openai/gsm8k",
                        "file_path": "data/train.jsonl",
                        "file_name": "train.jsonl",
                        "file_size": 5000,
                        "human_size": "4.9KB",
                        "file_type": "text",
                        "preview": {
                            "type": "text",
                            "lines": preview_lines,
                            "preview_lines": 2,
                            "total_lines": 100,
                        },
                        "truncated": True,
                    },
                },
            )
        if url.endswith("/datasets"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {"datasets": ["openai/gsm8k"], "total": 1},
                },
            )
        raise AssertionError(f"unexpected GET URL: {url}")

    monkeypatch.setattr(httpx, "get", fake_get)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="openai/gsm8k/data/train.jsonl", n=5),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.source == "shared"
    assert observation.preview_truncated is True
    assert len(observation.sample_rows) == 2
    assert observation.sample_rows[0]["prompt"] == "p1"
    assert "Shared dataset preview" in observation.text


def test_shared_preview_dataset_only_auto_selects_file(monkeypatch, tmp_path):
    """When only dataset name is given, auto-select first previewable file."""

    def fake_get(url, *, headers, params=None, timeout):
        if url.endswith("/datasets"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "datasets": ["pyromind/alpaca-gpt4-llm-demo"],
                        "total": 1,
                    },
                },
            )
        if "/datasets/files" in url:
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "dataset": "pyromind/alpaca-gpt4-llm-demo",
                        "files": [
                            {
                                "path": "alpaca_gpt4_demo.jsonl",
                                "name": "alpaca_gpt4_demo.jsonl",
                                "size": 1633696,
                                "human_size": "1.6MB",
                                "type": "text",
                            },
                        ],
                        "total_files": 1,
                        "truncated": False,
                    },
                },
            )
        if "/datasets/preview" in url:
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "dataset": "pyromind/alpaca-gpt4-llm-demo",
                        "file_path": "alpaca_gpt4_demo.jsonl",
                        "file_name": "alpaca_gpt4_demo.jsonl",
                        "file_size": 1633696,
                        "human_size": "1.6MB",
                        "file_type": "text",
                        "preview": {
                            "type": "text",
                            "lines": ['{"id":"alpaca-0","text":"hello"}'],
                            "preview_lines": 1,
                            "total_lines": 5000,
                        },
                        "truncated": True,
                    },
                },
            )
        raise AssertionError(f"unexpected GET URL: {url}")

    monkeypatch.setattr(httpx, "get", fake_get)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="pyromind/alpaca-gpt4-llm-demo"),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.source == "shared"
    assert observation.preview_file_path == "alpaca_gpt4_demo.jsonl"
    assert len(observation.sample_rows) == 1


def test_shared_preview_multiple_files_shows_list(monkeypatch, tmp_path):
    """When dataset has multiple files, observation includes file list."""

    def fake_get(url, *, headers, params=None, timeout):
        if url.endswith("/datasets"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {"datasets": ["org/multi"], "total": 1},
                },
            )
        if "/datasets/files" in url:
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "dataset": "org/multi",
                        "files": [
                            {
                                "path": "train.jsonl",
                                "name": "train.jsonl",
                                "size": 1000,
                                "human_size": "1000B",
                                "type": "text",
                            },
                            {
                                "path": "test.jsonl",
                                "name": "test.jsonl",
                                "size": 500,
                                "human_size": "500B",
                                "type": "text",
                            },
                        ],
                        "total_files": 2,
                        "truncated": False,
                    },
                },
            )
        if "/datasets/preview" in url:
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "dataset": "org/multi",
                        "file_path": "train.jsonl",
                        "file_name": "train.jsonl",
                        "file_size": 1000,
                        "human_size": "1000B",
                        "file_type": "text",
                        "preview": {
                            "type": "text",
                            "lines": ['{"x":1}'],
                            "preview_lines": 1,
                            "total_lines": 10,
                        },
                        "truncated": False,
                    },
                },
            )
        raise AssertionError(f"unexpected GET URL: {url}")

    monkeypatch.setattr(httpx, "get", fake_get)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="org/multi"),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.source == "shared"
    assert "2 files" in observation.text
    assert "train.jsonl" in observation.text
    assert "test.jsonl" in observation.text


def test_shared_preview_falls_back_to_storage(monkeypatch, tmp_path):
    """When shared datasets don't match, falls back to user storage."""
    content = b'{"a":1}\n{"a":2}\n'

    def fake_get(url, *, headers, params=None, timeout):
        return _Response(
            200,
            {"success": True, "data": {"datasets": ["openai/gsm8k"], "total": 1}},
        )

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "my_data.jsonl",
                        "size": len(content),
                        "content_type": "application/jsonl",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/f"}},
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        return _StreamResponse(content)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="datasets/my_data.jsonl"),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.source == "storage"
    assert observation.num_rows == 2


# ---------------------------------------------------------------------------
# User storage directory resolution tests
# ---------------------------------------------------------------------------


def test_storage_directory_multiple_files_asks_user(monkeypatch, tmp_path):
    """A storage folder with multiple files returns the file list, no preview."""
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "clean_script.py",
                                "path": "agentTest/clean_script.py",
                                "type": "File",
                                "size": 6854,
                                "last_modified": "2026-07-20 03:55:34",
                            },
                            {
                                "name": "test_data.jsonl",
                                "path": "agentTest/test_data.jsonl",
                                "type": "File",
                                "size": 4270,
                                "last_modified": "2026-07-20 03:55:34",
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    monkeypatch.setattr(httpx, "post", fake_post)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="agentTest/", n=5),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.source == "storage"
    assert observation.is_dir is True
    assert observation.num_rows is None
    assert observation.sample_rows == []
    assert "2 files" in observation.text
    assert "Ask the user which file to preview" in observation.text
    assert "agentTest/clean_script.py" in observation.text
    assert "agentTest/test_data.jsonl" in observation.text
    assert "6.7KB" in observation.text
    assert observation.files == [
        "agentTest/clean_script.py",
        "agentTest/test_data.jsonl",
    ]


def test_storage_directory_single_file_auto_previews(monkeypatch, tmp_path):
    """A storage folder with exactly one file previews it directly."""
    _patch_shared_empty(monkeypatch)
    content = b'{"a":1}\n{"a":2}\n'

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "only.jsonl",
                                "path": "solo/only.jsonl",
                                "type": "File",
                                "size": len(content),
                                "last_modified": "2026-07-20 03:55:34",
                            },
                        ]
                    },
                },
            )
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "solo/only.jsonl",
                        "size": len(content),
                        "content_type": "application/jsonl",
                        "is_dir": False,
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/f"}},
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        return _StreamResponse(content)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    conversation = _fake_conversation(tmp_path)

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="solo/", n=5),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.source == "storage"
    assert observation.preview_file_path == "solo/only.jsonl"
    assert observation.num_rows == 2


def test_storage_directory_lists_folders(monkeypatch, tmp_path):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "sample-a",
                                "path": "images/sample-a",
                                "type": "Folder",
                                "size": None,
                            },
                            {
                                "name": "labels.jsonl",
                                "path": "images/labels.jsonl",
                                "type": "File",
                                "size": 12,
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    monkeypatch.setattr(httpx, "post", fake_post)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="images/"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.is_dir is True
    assert observation.files == ["images/labels.jsonl"]
    assert observation.entries == [
        {
            "path": "images/sample-a",
            "name": "sample-a",
            "type": "folder",
            "size": None,
            "last_modified": None,
        },
        {
            "path": "images/labels.jsonl",
            "name": "labels.jsonl",
            "type": "file",
            "size": 12,
            "last_modified": None,
        },
    ]
    assert observation.directory_summary["detected_layout"] == "mixed_directory"
    assert observation.directory_summary["top_level_folder_count"] == 1
    assert observation.directory_summary["top_level_file_count"] == 1


def test_storage_directory_summary_detects_repeated_sample_folders(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            path = json["path"]
            if path == "aoi/":
                return _Response(
                    200,
                    {
                        "success": True,
                        "data": {
                            "list": [
                                {
                                    "name": "sample-a",
                                    "path": "aoi/sample-a",
                                    "type": "Folder",
                                },
                                {
                                    "name": "sample-b",
                                    "path": "aoi/sample-b",
                                    "type": "Folder",
                                },
                                {
                                    "name": "sample-c",
                                    "path": "aoi/sample-c",
                                    "type": "Folder",
                                },
                            ]
                        },
                    },
                )
            if path in {"aoi/sample-a", "aoi/sample-b", "aoi/sample-c"}:
                return _Response(
                    200,
                    {
                        "success": True,
                        "data": {
                            "list": [
                                {
                                    "name": "defect.jpg",
                                    "path": f"{path}/defect.jpg",
                                    "type": "File",
                                    "size": 10,
                                },
                                {
                                    "name": "diff.jpg",
                                    "path": f"{path}/diff.jpg",
                                    "type": "File",
                                    "size": 10,
                                },
                                {
                                    "name": "gt.jpg",
                                    "path": f"{path}/gt.jpg",
                                    "type": "File",
                                    "size": 10,
                                },
                                {
                                    "name": "meta.json",
                                    "path": f"{path}/meta.json",
                                    "type": "File",
                                    "size": 10,
                                },
                            ]
                        },
                    },
                )
        raise AssertionError(f"unexpected POST URL/path: {url} {json}")

    monkeypatch.setattr(httpx, "post", fake_post)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="aoi/"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.directory_summary["detected_layout"] == "repeated_sample_folders"
    assert observation.directory_summary["layout_confidence"] == "high"
    assert observation.directory_summary["top_level_folder_count"] == 3
    assert observation.directory_summary["sampled_child_folders"][0]["file_names"] == [
        "defect.jpg",
        "diff.jpg",
        "gt.jpg",
        "meta.json",
    ]
    assert "share files" in observation.directory_summary["layout_evidence"]


def test_storage_directory_summary_detects_flat_file_collection(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "front.jpg",
                                "path": "images/front.jpg",
                                "type": "File",
                                "size": 10,
                            },
                            {
                                "name": "back.png",
                                "path": "images/back.png",
                                "type": "File",
                                "size": 10,
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    monkeypatch.setattr(httpx, "post", fake_post)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="images/"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.directory_summary["detected_layout"] == "flat_file_collection"
    assert observation.directory_summary["layout_confidence"] == "medium"
    assert observation.directory_summary["top_level_type_counts"]["image"] == 2


def test_storage_directory_summary_detects_tabular_or_manifest_files(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "train.jsonl",
                                "path": "dataset/train.jsonl",
                                "type": "File",
                                "size": 10,
                            },
                            {
                                "name": "index.csv",
                                "path": "dataset/index.csv",
                                "type": "File",
                                "size": 10,
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    monkeypatch.setattr(httpx, "post", fake_post)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="dataset/"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert (
        observation.directory_summary["detected_layout"] == "tabular_or_manifest_files"
    )
    assert observation.directory_summary["top_level_type_counts"]["json"] == 1
    assert observation.directory_summary["top_level_type_counts"]["table"] == 1


def test_storage_directory_summary_detects_mixed_without_repeated_structure(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/file_list"):
            path = json["path"]
            if path == "mixed/":
                return _Response(
                    200,
                    {
                        "success": True,
                        "data": {
                            "list": [
                                {
                                    "name": "sample-a",
                                    "path": "mixed/sample-a",
                                    "type": "Folder",
                                },
                                {
                                    "name": "sample-b",
                                    "path": "mixed/sample-b",
                                    "type": "Folder",
                                },
                                {
                                    "name": "notes.txt",
                                    "path": "mixed/notes.txt",
                                    "type": "File",
                                    "size": 10,
                                },
                            ]
                        },
                    },
                )
            if path == "mixed/sample-a":
                files = ["front.jpg", "meta.json"]
            elif path == "mixed/sample-b":
                files = ["image.png", "label.txt"]
            else:
                raise AssertionError(f"unexpected child path: {path}")
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": name,
                                "path": f"{path}/{name}",
                                "type": "File",
                                "size": 10,
                            }
                            for name in files
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected POST URL/path: {url} {json}")

    monkeypatch.setattr(httpx, "post", fake_post)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="mixed/"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.directory_summary["detected_layout"] == "mixed_directory"
    assert observation.directory_summary["layout_confidence"] == "low"
    assert observation.directory_summary["repeated_file_name_set"] is None


def test_storage_virtual_directory_without_slash_falls_back_to_listing(
    monkeypatch,
    tmp_path,
):
    _patch_shared_empty(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(500, {}, text="file metadata is not found")
        if url.endswith("/file_list"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "defect.jpg",
                                "path": "images/sample-a/defect.jpg",
                                "type": "File",
                                "size": 12,
                            },
                            {
                                "name": "meta.json",
                                "path": "images/sample-a/meta.json",
                                "type": "File",
                                "size": 24,
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    monkeypatch.setattr(httpx, "post", fake_post)
    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="images/sample-a"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.is_dir is True
    assert len(observation.entries) == 2


def test_sample_mode_materializes_folder_and_runs_vision_preview(
    monkeypatch,
    tmp_path,
):
    image = b"\x89PNG\r\n\x1a\nfake"
    note = b"sample notes"
    downloads = {
        "https://download.test/image": image,
        "https://download.test/note": note,
    }

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            assert json["path"] == "/dataset/sample-a"
            return _Response(500, {}, text="file metadata is not found")
        if url.endswith("/file_list"):
            assert json["path"] == "/dataset/sample-a"
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "list": [
                            {
                                "name": "diagram.png",
                                "path": "/dataset/sample-a/diagram.png",
                                "type": "File",
                                "size": len(image),
                            },
                            {
                                "name": "note.txt",
                                "path": "/dataset/sample-a/note.txt",
                                "type": "File",
                                "size": len(note),
                            },
                        ]
                    },
                },
            )
        if url.endswith("/get_url"):
            suffix = "image" if json["path"].endswith(".png") else "note"
            return _Response(
                200,
                {
                    "success": True,
                    "data": {"url": f"https://download.test/{suffix}"},
                },
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        assert method == "GET"
        return _StreamResponse(downloads[url])

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    monkeypatch.setattr(
        "openhands.tools.pyromind_dataset.definition._call_vision_preview_model",
        lambda **kwargs: "OCR: triangle ABC",
    )

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(
            dataset_path="/dataset/",
            mode="sample",
            sample_paths=["/dataset/sample-a/"],
        ),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.previewed_rows == 1
    assert observation.has_vision is True
    assert observation.sample_manifest_path is not None
    manifest_path = tmp_path / observation.sample_manifest_path
    manifest = jsonlib.loads(manifest_path.read_text().strip())
    assert manifest["source_path"] == "/dataset/sample-a"
    assert manifest["local_path"].endswith("sample-a")
    assert manifest["images"][0].endswith("diagram.png")
    assert len(manifest["files"]) == 2
    assert (manifest_path.parent / manifest["images"][0]).read_bytes() == image
    assert (tmp_path / observation.local_sample_paths[0]).is_dir()
    assert observation.vision_previews[0]["ocr_text"] == "OCR: triangle ABC"
    assert any(item.type == "image" for item in observation.content)
    assert all(item.type == "text" for item in observation.to_llm_content)


def test_sample_mode_allows_conversation_public_data_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace" / "conversations" / "conversation-id"
    (workspace / "events").mkdir(parents=True)
    (workspace / "public_data").mkdir()

    resolved = _resolve_workspace_dir(cast(Any, _fake_conversation(workspace)))

    assert resolved == workspace.resolve()


def test_inspect_storage_image_uses_vision_model(monkeypatch, tmp_path) -> None:
    _patch_shared_empty(monkeypatch)
    image = b"\xff\xd8\xff\xe0fake-jpeg"

    def fake_post(url, *, headers, json, timeout):
        if url.endswith("/get_file_metadata"):
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "object_name": "/dataset/defect.jpg",
                        "bucket_name": "1001",
                        "size": len(image),
                        "content_type": "image/jpeg",
                        "is_dir": False,
                        "metadata": {},
                    },
                },
            )
        if url.endswith("/get_url"):
            return _Response(
                200,
                {"success": True, "data": {"url": "https://download.test/image"}},
            )
        raise AssertionError(f"unexpected POST URL: {url}")

    def fake_stream(method, url, *, headers, timeout, follow_redirects):
        assert method == "GET"
        assert url == "https://download.test/image"
        return _StreamResponse(image)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    monkeypatch.setattr(
        "openhands.tools.pyromind_dataset.definition._call_vision_preview_model",
        lambda **kwargs: "AOI image without visible text; red defect box.",
    )

    observation = PreviewDatasetExecutor(
        storage_base_url="https://portal.test/storage_api",
    )(
        PreviewDatasetAction(dataset_path="/dataset/defect.jpg"),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.has_vision is True
    assert observation.vision_previews[0]["ocr_text"].startswith("AOI image")
    assert "vision_summary=AOI image" in observation.text
    assert any(item.type == "image" for item in observation.content)
    assert all(item.type == "text" for item in observation.to_llm_content)
