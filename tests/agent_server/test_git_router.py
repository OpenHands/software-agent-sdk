"""Tests for git_router.py endpoints."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.git.models import (
    GitChange,
    GitChangeStatus,
    GitCommit,
    GitCommitsPage,
    GitDiff,
)


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

        mock_git_changes.assert_called_once_with(Path(test_path), ref=None)


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
    """Test that unexpected git failures still surface as 500."""
    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.side_effect = RuntimeError("unexpected failure")

        response = client.get("/api/git/changes", params={"path": "nonexistent/repo"})

        assert response.status_code == 500


@pytest.mark.asyncio
async def test_git_changes_query_param_with_command_error(client):
    """Test git changes returns 400 for GitCommandError."""
    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.side_effect = GitCommandError(
            message="git diff failed",
            command=["git", "diff"],
            exit_code=128,
            stderr="fatal: bad revision",
        )

        response = client.get("/api/git/changes", params={"path": "broken/repo"})

        assert response.status_code == 400
        assert "git diff failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_git_changes_returns_empty_list_when_path_is_not_git_repo(client):
    """Non-repo workspaces should yield 200 + [] instead of 500.

    Reproduces the v1-conversation bug where the workspace dir exists but
    has never been `git init`-ed: the endpoint must not crash the
    Changes tab.
    """
    # Arrange
    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.side_effect = GitRepositoryError(
            "Not a git repository: /Users/hieple/.openhands/agent-server-gui"
        )

        # Act
        response = client.get(
            "/api/git/changes",
            params={"path": "/Users/hieple/.openhands/agent-server-gui"},
        )

        # Assert
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.asyncio
async def test_git_diff_returns_empty_diff_when_path_is_not_git_repo(client):
    """Non-repo paths to /api/git/diff should yield 200 with null fields."""
    # Arrange
    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.side_effect = GitRepositoryError(
            "Not a git repository: /tmp/not-a-repo"
        )

        # Act
        response = client.get(
            "/api/git/diff", params={"path": "/tmp/not-a-repo/file.py"}
        )

        # Assert
        assert response.status_code == 200
        body = response.json()
        assert body["modified"] is None
        assert body["original"] is None


@pytest.mark.asyncio
async def test_git_changes_query_param_ref_head_on_empty_repo_returns_200(
    client, tmp_path
):
    """End-to-end: ``?ref=HEAD`` on a freshly init'd repo must return 200.

    Real git repo (no mock) so the SDK fix is exercised through the router.
    Reproduces the bug: before the fix this returned 400 with
    ``Git command failed: git --no-pager rev-parse --verify 'HEAD^{commit}'``.
    """
    # Arrange: real empty git repo with a single untracked file.
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "untracked.txt").write_text("new")

    # Act
    response = client.get(
        "/api/git/changes",
        params={"path": str(tmp_path), "ref": "HEAD"},
    )

    # Assert
    assert response.status_code == 200
    assert response.json() == [{"status": "ADDED", "path": "untracked.txt"}]


@pytest.mark.asyncio
async def test_git_changes_query_param_ref_head_on_orphan_branch_returns_200(
    client, tmp_path
):
    """End-to-end: ``?ref=HEAD`` on an orphan branch must return 200.

    Real git repo (no mock) so the SDK fix is exercised through the router.
    The repo has a commit on ``main``, but HEAD is currently pointing at an
    unborn orphan branch — exactly the user-reported state that surfaced as
    ``400 Bad Request: Git command failed: git --no-pager rev-parse --verify
    'HEAD^{commit}'`` in the Changes tab. The earlier ``_repo_has_commits``
    short-circuit doesn't catch this case (commits exist on main), so the
    fix has to come from the ``rev-parse`` failure handler instead.
    """

    # Arrange: repo with one commit on main, then switch to an orphan branch.
    def run_git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")
    (tmp_path / "committed.txt").write_text("on main")
    run_git("add", ".")
    run_git("commit", "-m", "on main")
    run_git("checkout", "--orphan", "orphan")
    run_git("rm", "-rf", "--cached", ".")
    (tmp_path / "untracked.txt").write_text("new")

    # Act
    response = client.get(
        "/api/git/changes",
        params={"path": str(tmp_path), "ref": "HEAD"},
    )

    # Assert
    assert response.status_code == 200
    paths = {entry["path"] for entry in response.json()}
    assert "untracked.txt" in paths


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
        mock_git_changes.assert_called_once_with(Path(test_path), ref=None)


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
        mock_git_diff.assert_called_once_with(Path(test_path), ref=None)


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
async def test_git_diff_query_param_with_command_error(client):
    """Test git diff returns 400 for GitCommandError."""
    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.side_effect = GitCommandError(
            message="git diff failed",
            command=["git", "diff"],
            exit_code=128,
            stderr="fatal: bad revision",
        )

        response = client.get("/api/git/diff", params={"path": "broken/file.py"})

        assert response.status_code == 400
        assert "git diff failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_git_diff_missing_path_param(client):
    """Test git diff endpoint returns 422 when path parameter is missing."""
    response = client.get("/api/git/diff")

    assert response.status_code == 422


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


@pytest.mark.asyncio
async def test_git_changes_forwards_ref_query_param(client):
    """The ``ref`` query param should be plumbed through to ``get_git_changes``."""
    with patch("openhands.agent_server.git_router.get_git_changes") as mock_git_changes:
        mock_git_changes.return_value = []

        test_path = "src/test_repo"
        response = client.get(
            "/api/git/changes", params={"path": test_path, "ref": "HEAD"}
        )

        assert response.status_code == 200
        mock_git_changes.assert_called_once_with(Path(test_path), ref="HEAD")


@pytest.mark.asyncio
async def test_git_diff_forwards_ref_query_param(client):
    """The ``ref`` query param should be plumbed through to ``get_git_diff``."""
    with patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff:
        mock_git_diff.return_value = GitDiff(modified="m", original="o")

        test_path = "src/test_file.py"
        response = client.get(
            "/api/git/diff",
            params={"path": test_path, "ref": "abc1234"},
        )

        assert response.status_code == 200
        mock_git_diff.assert_called_once_with(Path(test_path), ref="abc1234")


def test_git_endpoints_expose_ref_query_param(client):
    """OpenAPI schema should advertise the new optional ``ref`` query param."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    for endpoint in ("/api/git/changes", "/api/git/diff"):
        params = paths[endpoint]["get"]["parameters"]
        ref_param = next((p for p in params if p["name"] == "ref"), None)
        assert ref_param is not None, f"ref param missing on {endpoint}"
        assert ref_param["in"] == "query"
        assert ref_param.get("required", False) is False


def test_git_legacy_routes_are_removed_from_openapi(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_paths = response.json()["paths"]
    assert "/api/git/changes/{path}" not in openapi_paths
    assert "/api/git/diff/{path}" not in openapi_paths


# =============================================================================
# Commit History Tests
# =============================================================================


_SAMPLE_COMMIT = GitCommit(
    sha="a" * 40,
    short_sha="aaaaaaa",
    subject="add logging",
    author="Agent",
    timestamp="2026-07-10T12:00:00+07:00",
)


@pytest.mark.asyncio
async def test_git_commits_query_success(client):
    """The commits endpoint forwards to the SDK and serializes the page."""
    with patch("openhands.agent_server.git_router.get_git_commits") as mock_commits:
        mock_commits.return_value = GitCommitsPage(
            commits=[_SAMPLE_COMMIT], has_more=True
        )

        response = client.get("/api/git/commits", params={"path": "src/repo"})

        assert response.status_code == 200
        assert response.json() == {
            "commits": [
                {
                    "sha": "a" * 40,
                    "short_sha": "aaaaaaa",
                    "subject": "add logging",
                    "author": "Agent",
                    "timestamp": "2026-07-10T12:00:00+07:00",
                }
            ],
            "has_more": True,
        }
        mock_commits.assert_called_once_with(Path("src/repo"), limit=50)


@pytest.mark.asyncio
async def test_git_commits_query_not_a_repo_returns_empty_page(client):
    """A non-repo workspace yields an empty page, not an error."""
    with patch("openhands.agent_server.git_router.get_git_commits") as mock_commits:
        mock_commits.side_effect = GitRepositoryError("not a git repository")

        response = client.get("/api/git/commits", params={"path": "/not-a-repo"})

        assert response.status_code == 200
        assert response.json() == {"commits": [], "has_more": False}


@pytest.mark.asyncio
async def test_git_commit_changes_query_success(client):
    """The per-commit changes endpoint forwards the sha and repo path."""
    sha = "b" * 40
    with patch("openhands.agent_server.git_router.get_commit_changes") as mock_changes:
        mock_changes.return_value = [
            GitChange(status=GitChangeStatus.DELETED, path=Path("doomed.txt"))
        ]

        response = client.get(
            f"/api/git/commits/{sha}/changes", params={"path": "src/repo"}
        )

        assert response.status_code == 200
        assert response.json() == [{"status": "DELETED", "path": "doomed.txt"}]
        mock_changes.assert_called_once_with(Path("src/repo"), sha)


def test_git_commit_changes_query_malformed_sha_is_rejected(client):
    """A non-hex sha fails validation before it can reach git argv."""
    response = client.get(
        "/api/git/commits/not-a-sha/changes", params={"path": "src/repo"}
    )

    assert response.status_code == 422


def test_git_diff_query_rejects_ref_and_commit_together(client):
    """``ref`` and ``commit`` are mutually exclusive on /diff."""
    response = client.get(
        "/api/git/diff",
        params={"path": "src/repo/f.txt", "ref": "HEAD", "commit": "a" * 40},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_git_diff_query_commit_param_uses_commit_file_diff(client):
    """``commit=`` routes to the git-object diff, not the working-tree diff."""
    sha = "c" * 40
    with (
        patch(
            "openhands.agent_server.git_router.get_commit_file_diff"
        ) as mock_commit_diff,
        patch("openhands.agent_server.git_router.get_git_diff") as mock_git_diff,
    ):
        mock_commit_diff.return_value = GitDiff(modified="", original="contents")

        response = client.get(
            "/api/git/diff", params={"path": "src/repo/doomed.txt", "commit": sha}
        )

        assert response.status_code == 200
        assert response.json() == {"modified": "", "original": "contents"}
        mock_commit_diff.assert_called_once_with(Path("src/repo/doomed.txt"), sha)
        mock_git_diff.assert_not_called()


def test_git_commit_endpoints_in_openapi(client):
    """The schema advertises the commit routes and the /diff commit param."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert "/api/git/commits" in paths
    assert "/api/git/commits/{sha}/changes" in paths
    diff_params = paths["/api/git/diff"]["get"]["parameters"]
    commit_param = next((p for p in diff_params if p["name"] == "commit"), None)
    assert commit_param is not None
    assert commit_param["in"] == "query"
    assert commit_param.get("required", False) is False
