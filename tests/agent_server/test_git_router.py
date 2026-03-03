"""Tests for git_router.py endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.git.models import GitChange, GitChangeStatus, GitDiff


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    config = Config(session_api_keys=[])  # Disable authentication
    return TestClient(create_app(config), raise_server_exceptions=False)


# =============================================================================
# Query Parameter Tests (Preferred Method)
# =============================================================================


@pytest.mark.asyncio
async def test_git_changes_query_param_success(client):
    """Test successful git changes endpoint with query parameter."""
    expected_changes = [
        GitChange(status=GitChangeStatus.ADDED, path=Path("new_file.py")),
        GitChange(status=GitChangeStatus.UPDATED, path=Path("existing_file.py")),
        GitChange(status=GitChangeStatus.DELETED, path=Path("old_file.py")),
    ]

    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = expected_changes

        test_path = "src/test_repo"
        response = client.get("/api/git/changes", params={"path": test_path})

        assert response.status_code == 200
        response_data = response.json()

        assert len(response_data) == 3
        assert response_data[0]["status"] == "ADDED"
        assert response_data[0]["path"] == "new_file.py"
        assert response_data[1]["status"] == "UPDATED"
        assert response_data[1]["path"] == "existing_file.py"
        assert response_data[2]["status"] == "DELETED"
        assert response_data[2]["path"] == "old_file.py"

        mock_git_changes.assert_called_once_with(Path(test_path))


@pytest.mark.asyncio
async def test_git_changes_query_param_empty_result(client):
    """Test git changes endpoint with query parameter and no changes."""
    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = []

        test_path = "src/empty_repo"
        response = client.get("/api/git/changes", params={"path": test_path})

        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.asyncio
async def test_git_changes_query_param_with_exception(client):
    """Test git changes endpoint with query parameter when git operation fails."""
    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.side_effect = Exception("Git repository not found")

        test_path = "nonexistent/repo"
        response = client.get("/api/git/changes", params={"path": test_path})

        assert response.status_code == 500


@pytest.mark.asyncio
async def test_git_changes_missing_path_param(client):
    """Test git changes endpoint returns 422 when path parameter is missing."""
    response = client.get("/api/git/changes")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_git_changes_query_param_absolute_path(client):
    """Test git changes with query parameter and absolute path (main fix use case)."""
    expected_changes = [
        GitChange(status=GitChangeStatus.ADDED, path=Path("new_file.py")),
    ]

    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = expected_changes

        # This is the main use case - absolute paths with leading slash
        test_path = "/workspace/project"
        response = client.get("/api/git/changes", params={"path": test_path})

        assert response.status_code == 200
        assert len(response.json()) == 1
        mock_git_changes.assert_called_once_with(Path(test_path))


@pytest.mark.asyncio
async def test_git_diff_query_param_success(client):
    """Test successful git diff endpoint with query parameter."""
    expected_diff = GitDiff(
        modified="def new_function():\n    return 'updated'",
        original="def old_function():\n    return 'original'",
    )

    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.return_value = expected_diff

        test_path = "src/test_file.py"
        response = client.get("/api/git/diff", params={"path": test_path})

        assert response.status_code == 200
        response_data = response.json()

        assert response_data["modified"] == expected_diff.modified
        assert response_data["original"] == expected_diff.original
        mock_git_diff.assert_called_once_with(Path(test_path))


@pytest.mark.asyncio
async def test_git_diff_query_param_with_none_values(client):
    """Test git diff endpoint with query parameter and None values."""
    expected_diff = GitDiff(modified=None, original=None)

    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.return_value = expected_diff

        test_path = "nonexistent_file.py"
        response = client.get("/api/git/diff", params={"path": test_path})

        assert response.status_code == 200
        response_data = response.json()

        assert response_data["modified"] is None
        assert response_data["original"] is None


@pytest.mark.asyncio
async def test_git_diff_query_param_with_exception(client):
    """Test git diff endpoint with query parameter when git operation fails."""
    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.side_effect = Exception("Git diff failed")

        test_path = "nonexistent/file.py"
        response = client.get("/api/git/diff", params={"path": test_path})

        assert response.status_code == 500


@pytest.mark.asyncio
async def test_git_diff_missing_path_param(client):
    """Test git diff endpoint returns 422 when path parameter is missing."""
    response = client.get("/api/git/diff")

    assert response.status_code == 422


# =============================================================================
# Path Parameter Tests (Legacy/Backwards Compatibility)
# =============================================================================


@pytest.mark.asyncio
async def test_git_changes_path_param_success(client):
    """Test git changes endpoint with path parameter (legacy)."""
    expected_changes = [
        GitChange(status=GitChangeStatus.ADDED, path=Path("new_file.py")),
        GitChange(status=GitChangeStatus.UPDATED, path=Path("existing_file.py")),
    ]

    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = expected_changes

        test_path = "src/test_repo"
        response = client.get(f"/api/git/changes/{test_path}")

        assert response.status_code == 200
        response_data = response.json()

        assert len(response_data) == 2
        assert response_data[0]["status"] == "ADDED"
        assert response_data[1]["status"] == "UPDATED"
        mock_git_changes.assert_called_once_with(Path(test_path))


@pytest.mark.asyncio
async def test_git_changes_path_param_nested(client):
    """Test git changes endpoint with nested path parameter."""
    expected_changes = [
        GitChange(status=GitChangeStatus.ADDED, path=Path("file.py")),
    ]

    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = expected_changes

        test_path = "src/deep/nested/repo"
        response = client.get(f"/api/git/changes/{test_path}")

        assert response.status_code == 200
        mock_git_changes.assert_called_once_with(Path(test_path))


@pytest.mark.asyncio
async def test_git_diff_path_param_success(client):
    """Test git diff endpoint with path parameter (legacy)."""
    expected_diff = GitDiff(modified="new content", original="old content")

    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.return_value = expected_diff

        test_path = "src/test_file.py"
        response = client.get(f"/api/git/diff/{test_path}")

        assert response.status_code == 200
        response_data = response.json()

        assert response_data["modified"] == "new content"
        assert response_data["original"] == "old content"
        mock_git_diff.assert_called_once_with(Path(test_path))


@pytest.mark.asyncio
async def test_git_diff_path_param_nested(client):
    """Test git diff endpoint with nested path parameter."""
    expected_diff = GitDiff(modified="updated", original="original")

    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.return_value = expected_diff

        test_path = "src/utils/helper.py"
        response = client.get(f"/api/git/diff/{test_path}")

        assert response.status_code == 200
        mock_git_diff.assert_called_once_with(Path(test_path))


# =============================================================================
# Additional Edge Case Tests
# =============================================================================


@pytest.mark.asyncio
async def test_git_changes_with_all_status_types(client):
    """Test git changes endpoint with all possible GitChangeStatus values."""
    expected_changes = [
        GitChange(status=GitChangeStatus.ADDED, path=Path("added.py")),
        GitChange(status=GitChangeStatus.UPDATED, path=Path("updated.py")),
        GitChange(status=GitChangeStatus.DELETED, path=Path("deleted.py")),
        GitChange(status=GitChangeStatus.MOVED, path=Path("moved.py")),
    ]

    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = expected_changes

        test_path = "src/test_repo"
        response = client.get("/api/git/changes", params={"path": test_path})

        assert response.status_code == 200
        response_data = response.json()

        assert len(response_data) == 4
        assert response_data[0]["status"] == "ADDED"
        assert response_data[1]["status"] == "UPDATED"
        assert response_data[2]["status"] == "DELETED"
        assert response_data[3]["status"] == "MOVED"


@pytest.mark.asyncio
async def test_git_changes_with_complex_paths(client):
    """Test git changes endpoint with complex file paths."""
    expected_changes = [
        GitChange(
            status=GitChangeStatus.ADDED,
            path=Path("src/deep/nested/file.py"),
        ),
        GitChange(
            status=GitChangeStatus.UPDATED,
            path=Path("file with spaces.txt"),
        ),
        GitChange(
            status=GitChangeStatus.DELETED,
            path=Path("special-chars_file@123.py"),
        ),
    ]

    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = expected_changes

        test_path = "src/complex_repo"
        response = client.get("/api/git/changes", params={"path": test_path})

        assert response.status_code == 200
        response_data = response.json()

        assert len(response_data) == 3
        assert response_data[0]["path"] == "src/deep/nested/file.py"
        assert response_data[1]["path"] == "file with spaces.txt"
        assert response_data[2]["path"] == "special-chars_file@123.py"
