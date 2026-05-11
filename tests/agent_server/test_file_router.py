"""Tests for file_router.py endpoints."""

import asyncio
import io
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from openhands.agent_server import file_router as file_router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.file_router import _upload_file


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    config = Config(session_api_keys=[])  # Disable authentication
    return TestClient(create_app(config), raise_server_exceptions=False)


@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file for download tests."""
    test_file = tmp_path / "test_download.txt"
    test_file.write_text("test file content")
    return test_file


# =============================================================================
# Upload Tests - Query Parameter (Preferred Method)
# =============================================================================


def test_upload_file_query_param_success(client, tmp_path):
    """Test successful file upload with query parameter."""
    target_path = tmp_path / "uploaded_file.txt"
    file_content = b"test content for upload"

    response = client.post(
        "/api/file/upload",
        params={"path": str(target_path)},
        files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert target_path.exists()
    assert target_path.read_bytes() == file_content


def test_upload_file_query_param_creates_parent_dirs(client, tmp_path):
    """Test that upload creates parent directories if they don't exist."""
    target_path = tmp_path / "nested" / "dirs" / "file.txt"
    file_content = b"nested file content"

    response = client.post(
        "/api/file/upload",
        params={"path": str(target_path)},
        files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )

    assert response.status_code == 200
    assert target_path.exists()
    assert target_path.read_bytes() == file_content


def test_upload_file_query_param_relative_path_fails(client):
    """Test that upload with relative path returns 400."""
    response = client.post(
        "/api/file/upload",
        params={"path": "relative/path/file.txt"},
        files={"file": ("test.txt", io.BytesIO(b"content"), "text/plain")},
    )

    assert response.status_code == 400
    assert "must be absolute" in response.json()["detail"]


def test_upload_file_query_param_missing_path(client):
    """Test that upload without path parameter returns 422."""
    response = client.post(
        "/api/file/upload",
        files={"file": ("test.txt", io.BytesIO(b"content"), "text/plain")},
    )

    assert response.status_code == 422


def test_upload_file_query_param_missing_file(client, tmp_path):
    """Test that upload without file returns 422."""
    target_path = tmp_path / "missing_file.txt"

    response = client.post(
        "/api/file/upload",
        params={"path": str(target_path)},
    )

    assert response.status_code == 422


# =============================================================================
# Download Tests - Query Parameter (Preferred Method)
# =============================================================================


def test_download_file_query_param_success(client, temp_file):
    """Test successful file download with query parameter."""
    response = client.get(
        "/api/file/download",
        params={"path": str(temp_file)},
    )

    assert response.status_code == 200
    assert response.content == b"test file content"
    assert response.headers["content-type"] == "application/octet-stream"


def test_download_file_query_param_not_found(client, tmp_path):
    """Test download returns 404 when file doesn't exist."""
    nonexistent_path = tmp_path / "nonexistent.txt"

    response = client.get(
        "/api/file/download",
        params={"path": str(nonexistent_path)},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_download_file_query_param_relative_path_fails(client):
    """Test that download with relative path returns 400."""
    response = client.get(
        "/api/file/download",
        params={"path": "relative/path/file.txt"},
    )

    assert response.status_code == 400
    assert "must be absolute" in response.json()["detail"]


def test_download_file_query_param_directory_fails(client, tmp_path):
    """Test that download of directory returns 400."""
    response = client.get(
        "/api/file/download",
        params={"path": str(tmp_path)},
    )

    assert response.status_code == 400
    assert "not a file" in response.json()["detail"]


def test_download_file_query_param_missing_path(client):
    """Test that download without path parameter returns 422."""
    response = client.get("/api/file/download")

    assert response.status_code == 422


# =============================================================================
# Edge Case Tests
# =============================================================================


def test_upload_large_file_chunked(client, tmp_path):
    """Test that large files are uploaded correctly (chunked reading)."""
    target_path = tmp_path / "large_file.bin"
    # Create a file larger than the 8KB chunk size
    large_content = b"x" * (8192 * 3 + 100)  # About 24.5KB

    response = client.post(
        "/api/file/upload",
        params={"path": str(target_path)},
        files={
            "file": ("large.bin", io.BytesIO(large_content), "application/octet-stream")
        },
    )

    assert response.status_code == 200
    assert target_path.exists()
    assert target_path.read_bytes() == large_content


def test_upload_overwrites_existing_file(client, tmp_path):
    """Test that uploading to existing path overwrites the file."""
    target_path = tmp_path / "existing.txt"
    target_path.write_text("original content")

    new_content = b"new content"
    response = client.post(
        "/api/file/upload",
        params={"path": str(target_path)},
        files={"file": ("test.txt", io.BytesIO(new_content), "text/plain")},
    )

    assert response.status_code == 200
    assert target_path.read_bytes() == new_content


def test_download_preserves_filename(client, tmp_path):
    """Test that download response includes correct filename."""
    test_file = tmp_path / "my_document.pdf"
    test_file.write_bytes(b"pdf content")

    response = client.get(
        "/api/file/download",
        params={"path": str(test_file)},
    )

    assert response.status_code == 200
    assert "my_document.pdf" in response.headers.get("content-disposition", "")


def test_upload_file_with_special_characters_in_path(client, tmp_path):
    """Test upload with special characters in path (via query param)."""
    target_path = tmp_path / "file with spaces.txt"
    file_content = b"content with special path"

    response = client.post(
        "/api/file/upload",
        params={"path": str(target_path)},
        files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )

    assert response.status_code == 200
    assert target_path.exists()
    assert target_path.read_bytes() == file_content


def test_download_trajectory_uses_python_zipfile(client, monkeypatch, tmp_path):
    """Trajectory downloads should not depend on an OS-level zip command."""
    conversations_path = tmp_path / "conversations"
    conversation_id = uuid4()
    conversation_dir = conversations_path / conversation_id.hex
    nested_dir = conversation_dir / "nested"
    nested_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    (nested_dir / "event.json").write_text('{"id": "event-1"}')

    monkeypatch.setattr(
        "openhands.agent_server.file_router.get_default_config",
        lambda: Config(session_api_keys=[], conversations_path=conversations_path),
    )

    async def fail_if_shell_zip_is_used(*_args, **_kwargs):
        raise AssertionError("download_trajectory must not shell out to zip")

    monkeypatch.setattr(
        file_router_module,
        "bash_event_service",
        SimpleNamespace(start_bash_command=fail_if_shell_zip_is_used),
        raising=False,
    )

    response = client.get(f"/api/file/download-trajectory/{conversation_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read(f"{conversation_id.hex}/meta.json") == b"{}"
        assert archive.read(f"{conversation_id.hex}/nested/event.json") == (
            b'{"id": "event-1"}'
        )

    assert not (conversations_path / f"{conversation_id.hex}.zip").exists()


def test_download_file_with_special_characters_in_path(client, tmp_path):
    """Test download with special characters in path (via query param)."""
    test_file = tmp_path / "file with spaces.txt"
    test_file.write_text("special path content")

    response = client.get(
        "/api/file/download",
        params={"path": str(test_file)},
    )

    assert response.status_code == 200
    assert response.content == b"special path content"


def test_file_legacy_routes_are_removed_from_openapi(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_paths = response.json()["paths"]
    assert "/api/file/upload/{path}" not in openapi_paths
    assert "/api/file/download/{path}" not in openapi_paths


# =============================================================================
# search_subdirs Tests
# =============================================================================


def test_search_subdirs_returns_only_directories_with_absolute_paths(client, tmp_path):
    """Return subdirs with absolute paths; skip files and hidden entries."""
    (tmp_path / "repo1").mkdir()
    (tmp_path / "repo2").mkdir()
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / "README.md").write_text("hi")

    response = client.get("/api/file/search_subdirs", params={"path": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    names = [entry["name"] for entry in body["items"]]
    paths = [entry["path"] for entry in body["items"]]
    assert names == ["repo1", "repo2"]
    assert paths == [str(tmp_path / "repo1"), str(tmp_path / "repo2")]
    assert body["next_page_id"] is None


def test_search_subdirs_relative_path_returns_400(client):
    response = client.get("/api/file/search_subdirs", params={"path": "relative/path"})
    assert response.status_code == 400
    assert "must be absolute" in response.json()["detail"]


def test_search_subdirs_missing_directory_returns_404(client, tmp_path):
    response = client.get(
        "/api/file/search_subdirs",
        params={"path": str(tmp_path / "does-not-exist")},
    )
    assert response.status_code == 404


def test_search_subdirs_path_is_a_file_returns_400(client, tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("hi")
    response = client.get("/api/file/search_subdirs", params={"path": str(file_path)})
    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"]


def test_search_subdirs_paginates_with_limit_and_page_id(client, tmp_path):
    """Limit caps the page; next_page_id resumes from the next item."""
    for name in ["alpha", "Bravo", "charlie", "Delta", "echo"]:
        (tmp_path / name).mkdir()

    first = client.get(
        "/api/file/search_subdirs",
        params={"path": str(tmp_path), "limit": 2},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert [e["name"] for e in first_body["items"]] == ["alpha", "Bravo"]
    assert first_body["next_page_id"] == "charlie"

    second = client.get(
        "/api/file/search_subdirs",
        params={
            "path": str(tmp_path),
            "limit": 2,
            "page_id": first_body["next_page_id"],
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    assert [e["name"] for e in second_body["items"]] == ["charlie", "Delta"]
    assert second_body["next_page_id"] == "echo"

    third = client.get(
        "/api/file/search_subdirs",
        params={
            "path": str(tmp_path),
            "limit": 2,
            "page_id": second_body["next_page_id"],
        },
    )
    assert third.status_code == 200
    third_body = third.json()
    assert [e["name"] for e in third_body["items"]] == ["echo"]
    assert third_body["next_page_id"] is None


def test_search_subdirs_limit_too_low_returns_422(client, tmp_path):
    response = client.get(
        "/api/file/search_subdirs",
        params={"path": str(tmp_path), "limit": 0},
    )
    assert response.status_code == 422


def test_get_home_returns_user_home(client):
    response = client.get("/api/file/home")
    assert response.status_code == 200
    assert response.json()["home"] == str(Path.home())


def test_get_home_returns_dynamic_favorites_and_locations(
    client, tmp_path, monkeypatch
):
    # Arrange: pretend the user's home is tmp_path, populated with a mix of
    # visible dirs, a hidden dir, and a file. Favorites should include only
    # the visible dirs, alphabetised. Locations should report the POSIX root.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "projects").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "readme.txt").write_text("ignored")

    # Act
    response = client.get("/api/file/home")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["home"] == str(tmp_path)
    assert body["favorites"] == [
        {"label": "Documents", "path": str(tmp_path / "Documents")},
        {"label": "projects", "path": str(tmp_path / "projects")},
    ]
    assert body["locations"] == [{"label": "/", "path": "/"}]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "file_router.py:62-66 uses sync open()/f.write() inside an async "
        "handler. On slow storage every chunk write blocks the event loop. "
        "Tracked in https://github.com/OpenHands/software-agent-sdk/issues/3119."
    ),
)
@pytest.mark.timeout(20)
async def test_upload_does_not_block_event_loop_on_slow_storage(tmp_path, monkeypatch):
    # Drive _upload_file directly, not via ASGI: in-process ASGI interleaves
    # so cleanly that competing /health requests fit between writes, masking
    # the blocking. A background ticker on the same loop measures starvation.
    real_open = open

    class _SlowWriteFile:
        def __init__(self, real_file):
            self._f = real_file

        def write(self, data):
            time.sleep(0.1)  # models NFS / FUSE / encrypted FS write latency
            return self._f.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._f.close()

    def _slow_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        return _SlowWriteFile(f) if "w" in mode and "b" in mode else f

    monkeypatch.setattr(file_router_module, "open", _slow_open, raising=False)

    spooled = tempfile.SpooledTemporaryFile()
    spooled.write(b"x" * 64 * 1024)  # 8 × 8 KB chunks → ~800 ms of blocking
    spooled.seek(0)
    # SpooledTemporaryFile satisfies the BinaryIO protocol but isn't a nominal
    # subclass; UploadFile accepts it at runtime.
    upload = UploadFile(file=spooled, filename="uploaded.bin")  # pyright: ignore[reportArgumentType]

    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)

    ticker_task = asyncio.create_task(ticker())
    await asyncio.sleep(0.2)
    pre_ticks = len(ticks)

    upload_start = asyncio.get_event_loop().time()
    await _upload_file(str(tmp_path / "uploaded.bin"), upload)
    upload_end = asyncio.get_event_loop().time()

    await asyncio.sleep(0)
    stop.set()
    await ticker_task

    elapsed = upload_end - upload_start
    during_upload = sum(1 for t in ticks[pre_ticks:] if upload_start <= t < upload_end)
    expected_min = int((elapsed / 0.05) * 0.5)
    assert during_upload >= expected_min, (
        f"ticker logged {during_upload} ticks during {elapsed * 1000:.0f}ms "
        f"upload (expected ≥ {expected_min}); event loop is blocked by "
        f"sync f.write() at file_router.py:65."
    )
