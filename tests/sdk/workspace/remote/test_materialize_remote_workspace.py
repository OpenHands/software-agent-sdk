"""Materialization over a remote workspace transport.

The local-workspace tests in ``tests/sdk/llm/test_content_materialize.py`` cover
the decode/download/write logic against a ``LocalWorkspace``. This module fills
the remaining gap: ``write_bytes_to_workspace`` reaches the filesystem through
``workspace.file_upload`` and gates duplicate writes with
``workspace.execute_command("test -f ...")``. Those two calls behave differently
for a ``RemoteWorkspace`` than for a local one, so we exercise them here through
an in-memory fake remote transport rather than real HTTP.

The fake records every ``file_upload`` and honours the ``test -f`` existence
check, so we can assert the content-addressed destination path, that the bytes
are handed to the remote upload, and that content-addressing makes repeated
materialization idempotent (no duplicate remote writes).
"""

import base64
from pathlib import Path

import pytest
from pydantic import PrivateAttr

from openhands.sdk.llm import ImageContent, MaterializedRef
from openhands.sdk.llm.utils.content_materialize import MATERIALIZE_SUBDIR
from openhands.sdk.workspace import RemoteWorkspace
from openhands.sdk.workspace.models import CommandResult, FileOperationResult


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg=="
)


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class FakeRemoteWorkspace(RemoteWorkspace):
    """A ``RemoteWorkspace`` whose transport is an in-memory remote filesystem.

    Overriding only ``file_upload`` and ``execute_command`` keeps the real
    ``working_dir`` / path-building behaviour of ``RemoteWorkspace`` while
    letting the test observe exactly what the materialization layer sends over
    the wire.
    """

    _remote_files: dict[str, bytes] = PrivateAttr(default_factory=dict)
    _uploads: list[str] = PrivateAttr(default_factory=list)

    def file_upload(self, source_path, destination_path) -> FileOperationResult:  # type: ignore[override]
        data = Path(source_path).read_bytes()
        dest = str(destination_path)
        self._remote_files[dest] = data
        self._uploads.append(dest)
        return FileOperationResult(
            success=True,
            source_path=str(source_path),
            destination_path=dest,
            file_size=len(data),
        )

    def execute_command(self, command, cwd=None, timeout=30.0) -> CommandResult:  # type: ignore[override]
        # Only the content-addressed existence probe is exercised here.
        exists = False
        if command.startswith("test -f "):
            quoted = command[len("test -f ") :].strip()
            path = quoted.strip("'")
            exists = path in self._remote_files
        return CommandResult(
            command=command,
            exit_code=0 if exists else 1,
            stdout="",
            stderr="",
            timeout_occurred=False,
        )


@pytest.fixture
def remote_ws() -> FakeRemoteWorkspace:
    return FakeRemoteWorkspace(
        host="http://remote.invalid:8000", working_dir="/remote/project"
    )


def test_image_materialize_uploads_to_remote_workspace(
    remote_ws: FakeRemoteWorkspace,
) -> None:
    refs = ImageContent(image_urls=[_data_url(PNG_BYTES)]).materialize(remote_ws)

    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, MaterializedRef)

    # Written under the remote working dir's materialized subdir, content-addressed.
    assert ref.path.startswith(f"/remote/project/{MATERIALIZE_SUBDIR}/")
    assert ref.path.endswith(".png")
    assert ref.mime_type == "image/png"
    assert ref.size_bytes == len(PNG_BYTES)

    # Exactly one upload, carrying the real bytes to the content-addressed path.
    assert remote_ws._uploads == [ref.path]
    assert remote_ws._remote_files[ref.path] == PNG_BYTES


def test_image_materialize_is_idempotent_over_remote(
    remote_ws: FakeRemoteWorkspace,
) -> None:
    content = ImageContent(image_urls=[_data_url(PNG_BYTES)])

    first = content.materialize(remote_ws)
    second = content.materialize(remote_ws)

    # Content-addressed: both resolve to the same remote path.
    assert first[0].path == second[0].path
    # The ``test -f`` probe short-circuits the second write: only one upload.
    assert remote_ws._uploads == [first[0].path]
    assert len(remote_ws._remote_files) == 1


def test_distinct_content_yields_distinct_remote_paths(
    remote_ws: FakeRemoteWorkspace,
) -> None:
    other = PNG_BYTES + b"\x00extra"

    first = ImageContent(image_urls=[_data_url(PNG_BYTES)]).materialize(remote_ws)
    second = ImageContent(image_urls=[_data_url(other)]).materialize(remote_ws)

    assert first[0].path != second[0].path
    assert len(remote_ws._remote_files) == 2
    assert remote_ws._remote_files[first[0].path] == PNG_BYTES
    assert remote_ws._remote_files[second[0].path] == other
