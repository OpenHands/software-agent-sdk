"""Tests for file_router.py endpoints."""

import asyncio
import io
import json
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from openhands.agent_server import file_router as file_router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.file_router import ARCHIVE_MANIFEST_NAME, _upload_file


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


def test_search_subdirs_include_hidden_lists_dot_directories(client, tmp_path):
    """With include_hidden=true, dot-directories are listed (files still skipped)."""
    (tmp_path / "repo1").mkdir()
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / "README.md").write_text("hi")

    response = client.get(
        "/api/file/search_subdirs",
        params={"path": str(tmp_path), "include_hidden": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    names = [entry["name"] for entry in body["items"]]
    # Sorted case-insensitively; '.' sorts before alphanumerics.
    assert names == [".hidden_dir", "repo1"]


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


def test_get_home_include_hidden_lists_hidden_favorites(client, tmp_path, monkeypatch):
    # With include_hidden=true, hidden top-level directories appear in favorites
    # (files are still excluded).
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "projects").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "readme.txt").write_text("ignored")

    response = client.get("/api/file/home", params={"include_hidden": "true"})

    assert response.status_code == 200
    body = response.json()
    assert body["favorites"] == [
        {"label": ".cache", "path": str(tmp_path / ".cache")},
        {"label": "projects", "path": str(tmp_path / "projects")},
    ]


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


# =============================================================================
# Archive Tests - GET /api/file/archive (AGE-1871)
# =============================================================================


@pytest.fixture
def workspace(tmp_path):
    """A small nested workspace tree under ``tmp_path/project``."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "README.md").write_text("# project\n", encoding="utf-8")
    (root / "empty").mkdir()
    return root


def _git(args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@t.dev", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_archive_missing_path_returns_404(client, tmp_path):
    resp = client.get("/api/file/archive", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404


def test_archive_file_path_returns_400(client, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    resp = client.get("/api/file/archive", params={"path": str(f)})

    assert resp.status_code == 400
    assert "not a directory" in resp.json()["detail"].lower()


def test_archive_relative_path_returns_400(client):
    resp = client.get("/api/file/archive", params={"path": "relative/dir"})
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()


def test_archive_rejects_dashed_base_ref(client, workspace):
    resp = client.get(
        "/api/file/archive",
        params={"path": str(workspace), "format": "git-delta", "base_ref": "-x"},
    )
    assert resp.status_code == 400


def test_archive_cleans_up_temp_file(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")

    resp = client.get("/api/file/archive", params={"path": str(repo)})

    assert resp.status_code == 200
    # The scratch archive is built on the workspace volume (the target's parent)
    # rather than the system temp dir, and the BackgroundTask unlinks it once the
    # response is fully sent — so nothing is left behind next to the repo.
    leftovers = [p for p in repo.parent.iterdir() if p.name != "repo"]
    assert leftovers == []


def test_git_delta_new_repo_lists_files_as_additions(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "new_file.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/x-patch")
    patch = resp.content.decode("utf-8")
    assert "new_file.py" in patch
    assert "new file mode" in patch


def test_git_delta_captures_modifications_and_untracked_vs_head(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "a.txt").write_text("original\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "init"], root)
    # Mutate a tracked file and add an untracked one after the commit.
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    (root / "b.txt").write_text("brand new\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={"path": str(root), "format": "git-delta", "base_ref": "HEAD"},
    )

    assert resp.status_code == 200, resp.text
    patch = resp.content.decode("utf-8")
    assert "a.txt" in patch
    assert "b.txt" in patch
    assert "changed" in patch


def test_git_delta_excludes_node_modules_even_when_not_gitignored(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    # New source file (should be in the delta) + a heavy untracked node_modules
    # with NO .gitignore (should be excluded by the default excludes).
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "big.js").write_text(
        "// huge\n" * 100, encoding="utf-8"
    )

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    patch = resp.content.decode("utf-8")
    assert "app.py" in patch
    assert "node_modules" not in patch


def test_git_delta_can_disable_default_excludes(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "big.js").write_text(
        "// intentionally captured\n", encoding="utf-8"
    )

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "git-delta",
            "use_default_excludes": "false",
        },
    )

    assert resp.status_code == 200, resp.text
    patch = resp.content.decode("utf-8")
    assert "app.py" in patch
    assert "node_modules/pkg/big.js" in patch


def test_git_delta_scoped_to_requested_subdirectory(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "outside.txt").write_text("base\n", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "inside.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "init"], root)
    # Change a file inside the subdir AND one outside it, after the commit.
    (root / "outside.txt").write_text("changed-outside\n", encoding="utf-8")
    (sub / "inside.txt").write_text("changed-inside\n", encoding="utf-8")

    # Archiving the subdir must yield only the subdir's delta, not the repo's.
    resp = client.get(
        "/api/file/archive",
        params={"path": str(sub), "format": "git-delta", "base_ref": "HEAD"},
    )

    assert resp.status_code == 200, resp.text
    patch = resp.content.decode("utf-8")
    assert "inside.txt" in patch
    assert "outside.txt" not in patch


def test_git_delta_invalid_base_ref_returns_400(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "a.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "init"], root)

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "git-delta",
            "base_ref": "does-not-exist-ref",
        },
    )

    # A bad base_ref is client error, not a 500.
    assert resp.status_code == 400


def test_git_delta_on_non_repo_returns_400(client, workspace):
    resp = client.get(
        "/api/file/archive", params={"path": str(workspace), "format": "git-delta"}
    )
    assert resp.status_code == 400
    assert "git repositor" in resp.json()["detail"].lower()


def test_git_delta_response_includes_base_commit_header(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "a.txt").write_text("original\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "init"], root)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Working-tree change so the delta is non-empty.
    (root / "a.txt").write_text("changed\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={"path": str(root), "format": "git-delta", "base_ref": "HEAD"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["x-archive-base-commit"] == head_sha
    assert resp.headers["x-archive-base-ref"] == "HEAD"


def test_git_delta_new_repo_has_no_base_commit_header(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / "new_file.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    # Empty-tree base (no commits) is not a replayable commit, so no header.
    assert "x-archive-base-commit" not in resp.headers


def _repo_with_remote(root: Path, remote_url: str, branch: str = "feature-x") -> str:
    """Init a repo with one commit, an ``origin`` remote, and a named branch.

    Returns the HEAD commit sha.
    """
    root.mkdir()
    _git(["init"], root)
    _git(["remote", "add", "origin", remote_url], root)
    (root / "a.txt").write_text("original\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "init"], root)
    _git(["checkout", "-b", branch], root)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_git_delta_response_includes_repo_metadata_headers(client, tmp_path):
    head_sha = _repo_with_remote(
        tmp_path / "repo", "https://github.com/example/repo.git"
    )
    (tmp_path / "repo" / "a.txt").write_text("changed\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={"path": str(tmp_path / "repo"), "format": "git-delta"},
    )

    assert resp.status_code == 200, resp.text
    assert (
        resp.headers["x-archive-repo-remote"] == "https://github.com/example/repo.git"
    )
    assert resp.headers["x-archive-branch"] == "feature-x"
    assert resp.headers["x-archive-head-commit"] == head_sha


def test_tar_gz_response_includes_repo_metadata_headers(client, tmp_path):
    # The new repo-identity headers make a tar.gz snapshot self-describing too,
    # even though it carries no base_commit/base_ref.
    head_sha = _repo_with_remote(
        tmp_path / "repo", "https://github.com/example/repo.git"
    )

    resp = client.get(
        "/api/file/archive",
        params={"path": str(tmp_path / "repo"), "format": "tar.gz"},
    )

    assert resp.status_code == 200, resp.text
    assert (
        resp.headers["x-archive-repo-remote"] == "https://github.com/example/repo.git"
    )
    assert resp.headers["x-archive-branch"] == "feature-x"
    assert resp.headers["x-archive-head-commit"] == head_sha
    assert "x-archive-base-commit" not in resp.headers


def test_archive_redacts_remote_credentials_in_header(client, tmp_path):
    _repo_with_remote(
        tmp_path / "repo",
        "https://user:ghp_secrettoken@github.com/example/repo.git",
    )

    resp = client.get(
        "/api/file/archive",
        params={"path": str(tmp_path / "repo"), "format": "tar.gz"},
    )

    assert resp.status_code == 200, resp.text
    remote = resp.headers["x-archive-repo-remote"]
    assert "ghp_secrettoken" not in remote
    assert remote == "https://****@github.com/example/repo.git"


def test_archive_redacts_multiline_remote_credentials_in_header(client, tmp_path):
    _repo_with_remote(
        tmp_path / "repo",
        "https://user:ghp_secrettoken@github.com/example/repo.git\nextra",
    )

    resp = client.get(
        "/api/file/archive",
        params={"path": str(tmp_path / "repo"), "format": "tar.gz"},
    )

    assert resp.status_code == 200, resp.text
    remote = resp.headers["x-archive-repo-remote"]
    assert "ghp_secrettoken" not in remote
    assert remote == quote("https://****@github.com/example/repo.git\nextra", safe="")


def test_archive_detached_head_reports_detached_branch(client, tmp_path):
    head_sha = _repo_with_remote(
        tmp_path / "repo", "https://github.com/example/repo.git"
    )
    # Detach HEAD at the commit (SWE-bench-style pinned checkout).
    _git(["checkout", head_sha], tmp_path / "repo")

    resp = client.get(
        "/api/file/archive",
        params={"path": str(tmp_path / "repo"), "format": "tar.gz"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["x-archive-branch"] == "DETACHED"
    assert resp.headers["x-archive-head-commit"] == head_sha


def test_archive_non_git_directory_has_no_repo_metadata_headers(client, workspace):
    resp = client.get(
        "/api/file/archive", params={"path": str(workspace), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    assert "x-archive-repo-remote" not in resp.headers
    assert "x-archive-branch" not in resp.headers
    assert "x-archive-head-commit" not in resp.headers


def test_archive_remote_with_embedded_control_char_is_percent_encoded(client, tmp_path):
    # A remote URL is free-form text (unlike a ref name), so it can carry a
    # literal control character straight into what becomes a header value.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "remote.origin.url", "https://example.com/repo\nline-two"], repo)
    _git(["commit", "--allow-empty", "-m", "init"], repo)

    resp = client.get(
        "/api/file/archive", params={"path": str(repo), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    remote = resp.headers["x-archive-repo-remote"]
    assert "\n" not in remote
    assert remote == quote("https://example.com/repo\nline-two", safe="")


def test_archive_non_ascii_branch_is_percent_encoded_not_dropped(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["commit", "--allow-empty", "-m", "init"], repo)
    _git(["checkout", "-b", "café-branch"], repo)

    resp = client.get(
        "/api/file/archive", params={"path": str(repo), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["x-archive-branch"] == quote("café-branch", safe="")


# =============================================================================
# Archive Tests - format=tar.gz (full-workspace archive)
# =============================================================================


def _tar_members(content: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        return tar.getnames()


def test_tar_gz_on_non_git_directory_succeeds(client, workspace):
    # The key differentiator: git-delta 400s on a non-git dir, tar.gz captures
    # the whole tree.
    resp = client.get(
        "/api/file/archive", params={"path": str(workspace), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/gzip")
    names = _tar_members(resp.content)
    assert f"{workspace.name}/src/main.py" in names
    assert f"{workspace.name}/README.md" in names
    assert f"{workspace.name}/archive_manifest.json" in names


def test_tar_gz_default_excludes_drop_node_modules(client, tmp_path):
    root = tmp_path / "plain"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "junk.js").write_text("// big\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert f"{root.name}/src/main.py" in names
    assert not any("node_modules" in n for n in names)


def test_tar_gz_can_disable_default_excludes(client, tmp_path):
    root = tmp_path / "plain"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "junk.js").write_text("// big\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "tar.gz",
            "use_default_excludes": "false",
        },
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert f"{root.name}/src/main.py" in names
    assert f"{root.name}/node_modules/pkg/junk.js" in names

    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        member = tar.extractfile(f"{root.name}/archive_manifest.json")
        assert member is not None
        manifest = json.loads(member.read().decode("utf-8"))
    assert "node_modules/" not in manifest["excludes"]


def test_tar_gz_exclude_param_extends_defaults(client, tmp_path):
    root = tmp_path / "plain"
    (root / "keep").mkdir(parents=True)
    (root / "keep" / "a.txt").write_text("keep\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "token.txt").write_text("shh\n", encoding="utf-8")
    # A default-excluded dir to confirm defaults still apply alongside the param.
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_text("bytecode\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={"path": str(root), "format": "tar.gz", "exclude": "secrets"},
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert f"{root.name}/keep/a.txt" in names
    assert not any("secrets" in n for n in names)
    # Defaults still apply alongside the extra pattern.
    assert not any("__pycache__" in n for n in names)


def test_tar_gz_captures_gitignored_and_non_repo_files(client, tmp_path):
    # tar.gz is byte-for-byte: it captures files git-delta would miss, including
    # gitignored authored files (the dir need not even be a git repo).
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "ignored.txt").write_text("authored but gitignored\n", encoding="utf-8")
    (root / "tracked.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert f"{root.name}/ignored.txt" in names
    assert f"{root.name}/tracked.py" in names


def test_tar_gz_manifest_records_format_and_excludes(client, workspace):
    resp = client.get(
        "/api/file/archive",
        params={"path": str(workspace), "format": "tar.gz", "exclude": "*.bin"},
    )

    assert resp.status_code == 200, resp.text
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        member = tar.extractfile(f"{workspace.name}/archive_manifest.json")
        assert member is not None
        manifest = json.loads(member.read().decode("utf-8"))

    assert manifest["format"] == "tar.gz"
    assert manifest["source"] == workspace.name
    assert manifest["file_count"] >= 2
    # The applied excludes = defaults + the caller-supplied pattern.
    assert "*.bin" in manifest["excludes"]
    assert "node_modules/" in manifest["excludes"]


def test_tar_gz_ignores_base_ref_without_error(client, workspace):
    # base_ref applies only to git-delta; tar.gz must not error on it.
    resp = client.get(
        "/api/file/archive",
        params={"path": str(workspace), "format": "tar.gz", "base_ref": "HEAD"},
    )

    assert resp.status_code == 200, resp.text
    assert "x-archive-base-commit" not in resp.headers


def test_tar_gz_multi_segment_exclude_drops_nested_dir(client, tmp_path):
    # A multi-segment exclude (containing '/') must be honored for tar.gz, the
    # same way git-delta honors it via core.excludesFile. ``secrets/prod`` should
    # drop only that nested dir, while a same-named dir elsewhere is kept.
    root = tmp_path / "plain"
    (root / "secrets" / "prod").mkdir(parents=True)
    (root / "secrets" / "prod" / "token.txt").write_text("shh\n", encoding="utf-8")
    (root / "secrets" / "dev").mkdir(parents=True)
    (root / "secrets" / "dev" / "token.txt").write_text("ok\n", encoding="utf-8")
    (root / "prod").mkdir()  # same basename, different path: must be kept
    (root / "prod" / "keep.txt").write_text("keep\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={"path": str(root), "format": "tar.gz", "exclude": "secrets/prod"},
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    # The nested secrets/prod subtree is gone...
    assert not any("secrets/prod" in n for n in names)
    # ...but the sibling secrets/dev and the unrelated top-level prod survive.
    assert f"{root.name}/secrets/dev/token.txt" in names
    assert f"{root.name}/prod/keep.txt" in names


def test_tar_gz_bare_name_exclude_still_matches_any_component(client, tmp_path):
    # Bare-name excludes (no '/') must keep matching any path component at any
    # depth, so the multi-segment support does not regress node_modules-style
    # pruning.
    root = tmp_path / "plain"
    (root / "a" / "node_modules").mkdir(parents=True)
    (root / "a" / "node_modules" / "junk.js").write_text("// big\n", encoding="utf-8")
    (root / "keep.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "tar.gz",
            "use_default_excludes": "false",
            "exclude": "node_modules",
        },
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert not any("node_modules" in n for n in names)
    assert f"{root.name}/keep.py" in names


def test_tar_gz_preserves_user_archive_manifest_file(client, tmp_path, caplog):
    # If the workspace itself contains a top-level archive_manifest.json, the
    # synthetic capture manifest must NOT clobber it: the user's bytes win and a
    # warning is logged.
    root = tmp_path / "plain"
    root.mkdir()
    user_payload = b'{"this": "is the user\'s real file"}'
    (root / "archive_manifest.json").write_bytes(user_payload)
    (root / "other.txt").write_text("x\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        resp = client.get(
            "/api/file/archive", params={"path": str(root), "format": "tar.gz"}
        )

    assert resp.status_code == 200, resp.text
    arcname = f"{root.name}/archive_manifest.json"
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        # Exactly one member at the path (no shadowing duplicate).
        assert [n for n in tar.getnames() if n == arcname] == [arcname]
        member = tar.extractfile(arcname)
        assert member is not None
        assert member.read() == user_payload

    assert any(ARCHIVE_MANIFEST_NAME in record.message for record in caplog.records), (
        "expected a warning about the archive_manifest.json collision"
    )


# =============================================================================
# Archive Tests - git-delta auto-descend to the repo under the workspace base
# (AGE-1871 / infra#1444 H1). A repo-backed conversation clones into
# {base}/{repo_name}, so archiving the base itself must resolve down to the repo
# instead of 400-ing on the non-repo parent and capturing nothing.
# =============================================================================


def test_git_delta_auto_descends_to_single_repo_under_base(client, tmp_path):
    # The archive PATH points at the workspace base, but the git repo lives one
    # level below at {base}/{repo_name}. git-delta must resolve to it.
    base = tmp_path / "project"
    repo = base / "my-repo"
    repo.mkdir(parents=True)
    _git(["init"], repo)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(base), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/x-patch")
    assert "app.py" in resp.content.decode("utf-8")


def test_git_delta_auto_descends_two_levels_for_grouped_workspace(client, tmp_path):
    # Under sandbox grouping the repo is at {base}/{group}/{repo_name} — two
    # levels below the static base path runtime-api passes. Resolution is
    # bounded-depth, so it still finds it.
    base = tmp_path / "project"
    repo = base / "deadbeef" / "my-repo"
    repo.mkdir(parents=True)
    _git(["init"], repo)
    (repo / "app.py").write_text("y = 2\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(base), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    assert "app.py" in resp.content.decode("utf-8")


def test_git_delta_uses_base_directly_when_base_is_a_repo(client, tmp_path):
    # If the base IS itself a repo, use it directly without descending (the
    # resolver short-circuits), so a sibling subdir below it is irrelevant.
    base = tmp_path / "project"
    base.mkdir()
    _git(["init"], base)
    (base / "top.py").write_text("a = 1\n", encoding="utf-8")
    (base / "docs").mkdir()
    (base / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(base), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    patch = resp.content.decode("utf-8")
    assert "top.py" in patch
    assert "guide.md" in patch


def test_git_delta_ambiguous_multiple_repos_still_400s(client, tmp_path):
    # Two candidate repos under the base is ambiguous — do not guess; fall back
    # to the existing not-a-git-repo behavior (400) rather than archive a random
    # one.
    base = tmp_path / "project"
    for name in ("repo-a", "repo-b"):
        r = base / name
        r.mkdir(parents=True)
        _git(["init"], r)
        (r / "f.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(base), "format": "git-delta"}
    )

    assert resp.status_code == 400


def test_git_delta_no_repo_under_base_still_400s(client, tmp_path):
    base = tmp_path / "project"
    (base / "src").mkdir(parents=True)
    (base / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(base), "format": "git-delta"}
    )

    assert resp.status_code == 400


# =============================================================================
# Archive Tests - tar.gz never leaks .git credentials (infra#1444 H2)
# =============================================================================


def test_tar_gz_default_excludes_drop_dot_git(client, tmp_path):
    # The full-archive default excludes must drop the entire .git tree so a clone
    # token in .git/config never lands in the shared, indefinitely-retained
    # archive bucket (and the object DB does not blow up the archive size).
    root = tmp_path / "project"
    root.mkdir()
    _git(["init"], root)
    (root / "README.md").write_text("# p\n", encoding="utf-8")
    # Simulate a tokenized clone remote persisted by git.
    _git(
        ["remote", "add", "origin", "https://x-access-token:ghs_SECRET@github.com/o/r"],
        root,
    )

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert not any("/.git/" in n or n.endswith("/.git") for n in names)
    assert b"ghs_SECRET" not in resp.content
    assert f"{root.name}/README.md" in names


def test_tar_gz_full_capture_still_strips_git_credentials(client, tmp_path):
    # Even with use_default_excludes=false (a deliberate full capture), the
    # credential-bearing git internals must never be persisted.
    root = tmp_path / "project"
    root.mkdir()
    _git(["init"], root)
    (root / "README.md").write_text("# p\n", encoding="utf-8")
    _git(
        ["remote", "add", "origin", "https://x-access-token:ghs_SECRET@github.com/o/r"],
        root,
    )

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "tar.gz",
            "use_default_excludes": "false",
        },
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    # Full capture keeps most of .git (history/objects) for fidelity...
    assert any("/.git/" in n for n in names)
    # ...but never the secrets.
    assert not any(n.endswith("/.git/config") for n in names)
    assert not any("/.git/logs/" in n for n in names)
    assert b"ghs_SECRET" not in resp.content


def test_tar_gz_strips_credentials_file_and_reflog_with_tokens(client, tmp_path):
    # Beyond .git/config: a synthetic .git/ carrying a token in .git/credentials
    # and in a .git/logs/ reflog must never reach the archive bytes, even in a
    # full capture. Built by hand (not `git init`) so .git/credentials and
    # .git/logs/ — which git does not write on init — are exercised directly.
    root = tmp_path / "project"
    git_dir = root / ".git"
    (git_dir / "logs").mkdir(parents=True)
    (root / "README.md").write_text("# p\n", encoding="utf-8")
    (git_dir / "config").write_text(
        '[remote "origin"]\n'
        "\turl = https://x-access-token:ghs_CONFIGTOK@github.com/o/r\n",
        encoding="utf-8",
    )
    (git_dir / "credentials").write_text(
        "https://x-access-token:ghs_CREDTOK@github.com\n", encoding="utf-8"
    )
    (git_dir / "logs" / "HEAD").write_text(
        "0 1 t <t@t.dev> 0 +0000\tclone: from "
        "https://x-access-token:ghs_LOGTOK@github.com/o/r\n",
        encoding="utf-8",
    )
    # A non-credential .git internal must survive: proves the credential files
    # are stripped specifically, not the whole .git tree.
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "tar.gz",
            "use_default_excludes": "false",
        },
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert not any(n.endswith("/.git/config") for n in names)
    assert not any(n.endswith("/.git/credentials") for n in names)
    assert not any("/.git/logs/" in n for n in names)
    assert any(n.endswith("/.git/HEAD") for n in names)
    for token in (b"ghs_CONFIGTOK", b"ghs_CREDTOK", b"ghs_LOGTOK"):
        assert token not in resp.content


def test_tar_gz_strips_fetch_head_and_submodule_credentials(client, tmp_path):
    # enyst review (#3867): the strip must also cover .git/FETCH_HEAD (git writes
    # the fetched URL — token and all — there) and submodule git dirs under
    # .git/modules/<name>/, not just the top-level .git/config. Built by hand so
    # those exact leak surfaces are exercised in a full capture.
    root = tmp_path / "project"
    git_dir = root / ".git"
    sub = git_dir / "modules" / "libfoo"
    (sub / "logs").mkdir(parents=True)
    (root / "README.md").write_text("# p\n", encoding="utf-8")
    (git_dir / "FETCH_HEAD").write_text(
        "abc\tbranch 'main' of https://x-access-token:ghs_FETCHTOK@github.com/o/r\n",
        encoding="utf-8",
    )
    (sub / "config").write_text(
        '[remote "origin"]\n'
        "\turl = https://x-access-token:ghs_SUBCFGTOK@github.com/o/sub\n",
        encoding="utf-8",
    )
    (sub / "FETCH_HEAD").write_text(
        "def\tbranch 'main' of "
        "https://x-access-token:ghs_SUBFETCHTOK@github.com/o/sub\n",
        encoding="utf-8",
    )
    (sub / "logs" / "HEAD").write_text(
        "0 1 t <t@t.dev> 0 +0000\tclone: from "
        "https://x-access-token:ghs_SUBLOGTOK@github.com/o/sub\n",
        encoding="utf-8",
    )
    # A non-credential .git internal must still survive the strip.
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "tar.gz",
            "use_default_excludes": "false",
        },
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert not any(n.endswith("/.git/FETCH_HEAD") for n in names)
    assert not any("/.git/modules/" in n and n.endswith("/config") for n in names)
    assert not any("/.git/modules/" in n and n.endswith("/FETCH_HEAD") for n in names)
    assert not any("/.git/modules/libfoo/logs/" in n for n in names)
    assert any(n.endswith("/.git/HEAD") for n in names)
    for token in (
        b"ghs_FETCHTOK",
        b"ghs_SUBCFGTOK",
        b"ghs_SUBFETCHTOK",
        b"ghs_SUBLOGTOK",
    ):
        assert token not in resp.content


# =============================================================================
# Archive Tests - tar.gz empty-dir round-trip + per-segment exclude (infra#1444
# L4 / L5)
# =============================================================================


def test_tar_gz_preserves_empty_directories(client, tmp_path):
    # An authored-but-empty directory must round-trip (byte-for-byte capture).
    root = tmp_path / "project"
    (root / "outputs").mkdir(parents=True)  # empty, no files
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "tar.gz"}
    )

    assert resp.status_code == 200, resp.text
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        outputs = tar.getmember(f"{root.name}/outputs")
        assert outputs.isdir()


def test_tar_gz_multi_segment_exclude_does_not_cross_slash(client, tmp_path):
    # A '*' in a multi-segment exclude must not cross '/' (gitignore semantics,
    # matching git-delta). '*/secret.txt' drops a depth-2 match but keeps a
    # deeper one.
    root = tmp_path / "project"
    (root / "a").mkdir(parents=True)
    (root / "a" / "secret.txt").write_text("drop\n", encoding="utf-8")
    (root / "a" / "b").mkdir()
    (root / "a" / "b" / "secret.txt").write_text("keep\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive",
        params={
            "path": str(root),
            "format": "tar.gz",
            "exclude": "*/secret.txt",
        },
    )

    assert resp.status_code == 200, resp.text
    names = _tar_members(resp.content)
    assert f"{root.name}/a/secret.txt" not in names
    assert f"{root.name}/a/b/secret.txt" in names


def test_exact_size_reader_pads_short_and_caps_long():
    """_ExactSizeReader yields EXACTLY the declared size: pad short, cap long."""
    reader = file_router_module._ExactSizeReader
    short = reader(io.BytesIO(b"abc"), 5)
    chunks = []
    while chunk := short.read(2):
        chunks.append(chunk)
    assert b"".join(chunks) == b"abc\x00\x00"
    long = reader(io.BytesIO(b"abcdefgh"), 4)
    assert long.read(100) == b"abcd"
    assert long.read(100) == b""


def test_tar_stream_stays_aligned_when_source_shrinks(tmp_path):
    """A file truncated mid-add must not corrupt the rest of the tar stream.

    Regression for the catch-OSError-and-continue bug: tar.addfile writes the
    header (declared size) before copying the body, so a short read used to
    desync every following member and silently ship a corrupt archive.
    _ExactSizeReader pads the short source so the bytes written always match the
    header and the archive stays fully readable.
    """
    out = tmp_path / "a.tar.gz"
    body2 = b"second member intact"
    with tarfile.open(out, "w:gz") as tar:
        # Member one: header claims 10 bytes but the source only yields 3 (a file
        # truncated after its size was taken). The reader pads to 10.
        info1 = tarfile.TarInfo("one")
        info1.size = 10
        tar.addfile(info1, file_router_module._ExactSizeReader(io.BytesIO(b"abc"), 10))
        info2 = tarfile.TarInfo("two")
        info2.size = len(body2)
        tar.addfile(
            info2, file_router_module._ExactSizeReader(io.BytesIO(body2), len(body2))
        )
    # Full readback must succeed (no ReadError) and member two must be intact.
    with tarfile.open(out, "r:gz") as tar:
        assert tar.getnames() == ["one", "two"]
        member_two = tar.extractfile("two")
        assert member_two is not None
        assert member_two.read() == body2
        member_one = tar.extractfile("one")
        assert member_one is not None
        assert member_one.read() == b"abc" + b"\x00" * 7


def test_git_delta_dir_only_exclude_keeps_same_named_file(client, tmp_path):
    """A 'build/' default exclude drops the build/ dir but KEEPS a file named
    'build' — git-delta must match the tar.gz dir-only carve-out."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    # A regular file literally named 'build' must be KEPT...
    (root / "build").write_text("authored marker\n", encoding="utf-8")
    # ...while an actual build/ directory (the default 'build/' exclude) is dropped.
    (root / "sub" / "build").mkdir(parents=True)
    (root / "sub" / "build" / "out.o").write_text("compiled\n", encoding="utf-8")

    resp = client.get(
        "/api/file/archive", params={"path": str(root), "format": "git-delta"}
    )

    assert resp.status_code == 200, resp.text
    patch = resp.content.decode("utf-8")
    assert "diff --git a/build b/build" in patch  # the file is kept
    assert "authored marker" in patch
    assert "sub/build/out.o" not in patch  # the directory is excluded
