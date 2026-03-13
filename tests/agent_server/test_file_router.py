"""Tests for file_router.py endpoints."""

import io

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


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
# Upload Tests - Path Parameter (Legacy/Backwards Compatibility)
# =============================================================================


def test_upload_file_path_param_success(client, tmp_path):
    """Test successful file upload with path parameter (legacy)."""
    target_path = tmp_path / "uploaded_via_path.txt"
    file_content = b"legacy upload content"

    response = client.post(
        f"/api/file/upload/{target_path}",
        files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert target_path.exists()
    assert target_path.read_bytes() == file_content


def test_upload_file_path_param_nested(client, tmp_path):
    """Test file upload with nested path parameter."""
    target_path = tmp_path / "deep" / "nested" / "path" / "file.txt"
    file_content = b"deeply nested content"

    response = client.post(
        f"/api/file/upload/{target_path}",
        files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )

    assert response.status_code == 200
    assert target_path.exists()


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
# Download Tests - Path Parameter (Legacy/Backwards Compatibility)
# =============================================================================


def test_download_file_path_param_success(client, temp_file):
    """Test successful file download with path parameter (legacy)."""
    response = client.get(f"/api/file/download/{temp_file}")

    assert response.status_code == 200
    assert response.content == b"test file content"


def test_download_file_path_param_nested(client, tmp_path):
    """Test file download with nested path parameter."""
    nested_file = tmp_path / "nested" / "dir" / "file.txt"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("nested content")

    response = client.get(f"/api/file/download/{nested_file}")

    assert response.status_code == 200
    assert response.content == b"nested content"


def test_download_file_path_param_not_found(client, tmp_path):
    """Test download with path parameter returns 404 when file doesn't exist."""
    nonexistent = tmp_path / "does_not_exist.txt"

    response = client.get(f"/api/file/download/{nonexistent}")

    assert response.status_code == 404


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


def test_file_legacy_routes_are_deprecated_in_openapi(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_schema = response.json()

    upload_operation = openapi_schema["paths"]["/api/file/upload/{path}"]["post"]
    assert upload_operation.get("deprecated") is True
    assert "Deprecated since v1.15.0" in upload_operation["description"]

    download_operation = openapi_schema["paths"]["/api/file/download/{path}"]["get"]
    assert download_operation.get("deprecated") is True
    assert "Deprecated since v1.15.0" in download_operation["description"]
