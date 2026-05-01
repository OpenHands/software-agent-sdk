"""Tests for repository cloning and skill loading in OpenHandsCloudWorkspace."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.workspace.cloud.repo import (
    CloneResult,
    GitProvider,
    RepoMapping,
    RepoSource,
    _build_clone_url,
    _detect_provider_from_url,
    _extract_repo_name,
    _get_unique_dir_name,
    _is_commit_sha,
    _sanitize_dir_name,
    clone_repos,
    get_repos_context,
)


class TestRepoSource:
    """Tests for RepoSource model."""

    # --- Short URL format (requires provider) ---

    def test_short_url_with_provider(self):
        """Test RepoSource with short URL and explicit provider."""
        repo = RepoSource(url="owner/repo", provider="github")
        assert repo.url == "owner/repo"
        assert repo.provider == "github"
        assert repo.get_provider() == GitProvider.GITHUB

    def test_short_url_with_ref_and_provider(self):
        """Test RepoSource with short URL, ref, and provider."""
        repo = RepoSource(url="owner/repo", ref="main", provider="gitlab")
        assert repo.url == "owner/repo"
        assert repo.ref == "main"
        assert repo.get_provider() == GitProvider.GITLAB

    def test_short_url_without_provider_rejected(self):
        """Test that short URL without provider is rejected."""
        with pytest.raises(ValueError, match="requires explicit 'provider' field"):
            RepoSource(url="owner/repo")

    def test_short_url_string_without_provider_rejected(self):
        """Test that string input without provider is rejected."""
        with pytest.raises(ValueError, match="requires explicit 'provider' field"):
            RepoSource.model_validate("owner/repo")

    def test_short_url_dict_without_provider_rejected(self):
        """Test that dict input without provider is rejected."""
        with pytest.raises(ValueError, match="requires explicit 'provider' field"):
            RepoSource.model_validate({"url": "owner/repo", "ref": "v1.0"})

    # --- Full URL format (provider auto-detected) ---

    def test_full_https_url_github(self):
        """Test RepoSource with full GitHub HTTPS URL."""
        repo = RepoSource(url="https://github.com/owner/repo")
        assert repo.url == "https://github.com/owner/repo"
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.GITHUB

    def test_full_https_url_gitlab(self):
        """Test RepoSource with full GitLab HTTPS URL."""
        repo = RepoSource(url="https://gitlab.com/owner/repo")
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.GITLAB

    def test_full_https_url_bitbucket(self):
        """Test RepoSource with full Bitbucket HTTPS URL."""
        repo = RepoSource(url="https://bitbucket.org/owner/repo")
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.BITBUCKET

    def test_git_ssh_url(self):
        """Test RepoSource with git SSH URL (contains github.com)."""
        repo = RepoSource(url="git@github.com:owner/repo.git")
        assert repo.url == "git@github.com:owner/repo.git"
        assert repo.get_provider() == GitProvider.GITHUB

    # --- Provider field behavior ---

    def test_provider_explicit_overrides_detection(self):
        """Test that explicit provider is used even with full URL."""
        # User explicitly says gitlab even though URL is github
        # This could be intentional (mirror, etc.)
        repo = RepoSource(url="https://github.com/owner/repo", provider="gitlab")
        assert repo.get_provider() == GitProvider.GITLAB

    def test_provider_github_token_name(self):
        """Test GitHub token name."""
        repo = RepoSource(url="owner/repo", provider="github")
        assert repo.get_token_name() == "github_token"

    def test_provider_gitlab_token_name(self):
        """Test GitLab token name."""
        repo = RepoSource(url="owner/repo", provider="gitlab")
        assert repo.get_token_name() == "gitlab_token"

    def test_provider_bitbucket_token_name(self):
        """Test Bitbucket token name."""
        repo = RepoSource(url="owner/repo", provider="bitbucket")
        assert repo.get_token_name() == "bitbucket_token"

    # --- URL validation ---

    def test_invalid_url_rejected(self):
        """Test that invalid URLs are rejected."""
        with pytest.raises(ValueError, match="URL must be"):
            RepoSource(url="invalid-url-format", provider="github")

    def test_url_with_dots_allowed(self):
        """Test that URLs with dots in repo name are allowed."""
        repo = RepoSource(url="owner/repo.name", provider="github")
        assert repo.url == "owner/repo.name"

    def test_url_with_dashes_allowed(self):
        """Test that URLs with dashes are allowed."""
        repo = RepoSource(url="my-org/my-repo", provider="github")
        assert repo.url == "my-org/my-repo"


class TestProviderDetection:
    """Tests for provider detection from URLs."""

    def test_detect_github(self):
        assert _detect_provider_from_url("https://github.com/o/r") == GitProvider.GITHUB

    def test_detect_gitlab(self):
        assert _detect_provider_from_url("https://gitlab.com/o/r") == GitProvider.GITLAB

    def test_detect_bitbucket(self):
        assert (
            _detect_provider_from_url("https://bitbucket.org/o/r")
            == GitProvider.BITBUCKET
        )

    def test_detect_unknown(self):
        assert _detect_provider_from_url("https://example.com/o/r") is None
        assert _detect_provider_from_url("owner/repo") is None
        assert _detect_provider_from_url("https://dev.azure.com/o/p/_git/r") is None


class TestHelperFunctions:
    """Tests for helper functions in repo module."""

    def test_is_commit_sha_valid(self):
        """Test detection of valid commit SHAs."""
        assert _is_commit_sha("abc1234") is True
        assert (
            _is_commit_sha("abc1234567890abcdef1234567890abcdef12") is True
        )  # 40 chars
        assert _is_commit_sha("ABC1234") is True  # Case insensitive

    def test_is_commit_sha_invalid(self):
        """Test detection of invalid commit SHAs."""
        assert _is_commit_sha(None) is False
        assert _is_commit_sha("main") is False
        assert _is_commit_sha("v1.0.0") is False
        assert _is_commit_sha("abc123") is False  # Too short
        assert _is_commit_sha("xyz1234") is False  # Invalid hex chars

    def test_extract_repo_name_owner_repo(self):
        """Test extracting repo name from owner/repo format."""
        assert _extract_repo_name("owner/repo") == "repo"
        assert _extract_repo_name("my-org/my-repo") == "my-repo"

    def test_extract_repo_name_https_url(self):
        """Test extracting repo name from HTTPS URLs."""
        assert _extract_repo_name("https://github.com/owner/repo") == "repo"
        assert _extract_repo_name("https://github.com/owner/repo.git") == "repo"
        assert _extract_repo_name("https://gitlab.com/owner/repo") == "repo"

    def test_extract_repo_name_windows_file_url(self):
        """Test extracting repo names from Windows file URLs."""
        assert _extract_repo_name(r"file://C:\Users\user\work\repo") == "repo"

    def test_extract_repo_name_ssh_url(self):
        """Test extracting repo name from SSH URLs."""
        assert _extract_repo_name("git@github.com:owner/repo.git") == "repo"
        assert _extract_repo_name("git@gitlab.com:owner/repo") == "repo"

    def test_sanitize_dir_name(self):
        """Test directory name sanitization."""
        assert _sanitize_dir_name("repo") == "repo"
        assert _sanitize_dir_name("my-repo") == "my-repo"
        assert _sanitize_dir_name("my.repo") == "my.repo"
        assert _sanitize_dir_name("repo/name") == "repo_name"  # Invalid char
        assert _sanitize_dir_name("...repo...") == "repo"  # Trim dots
        assert _sanitize_dir_name("") == "repo"  # Empty -> default

    def test_get_unique_dir_name(self):
        """Test unique directory name generation."""
        existing: set[str] = set()
        assert _get_unique_dir_name("repo", existing) == "repo"

        existing = {"repo"}
        assert _get_unique_dir_name("repo", existing) == "repo_1"

        existing = {"repo", "repo_1", "repo_2"}
        assert _get_unique_dir_name("repo", existing) == "repo_3"

    def test_build_clone_url_github_owner_repo_no_token(self):
        """Test building clone URL from owner/repo without token."""
        url = _build_clone_url("owner/repo", GitProvider.GITHUB, None)
        assert url == "https://github.com/owner/repo.git"

    def test_build_clone_url_github_owner_repo_with_token(self):
        """Test building clone URL from owner/repo with GitHub token."""
        url = _build_clone_url("owner/repo", GitProvider.GITHUB, "ghtoken123")
        assert url == "https://ghtoken123@github.com/owner/repo.git"

    def test_build_clone_url_github_https_with_token(self):
        """Test building clone URL from GitHub HTTPS URL with token."""
        url = _build_clone_url(
            "https://github.com/owner/repo", GitProvider.GITHUB, "ghtoken123"
        )
        assert url == "https://ghtoken123@github.com/owner/repo"

    def test_build_clone_url_gitlab_owner_repo_with_token(self):
        """Test building clone URL from owner/repo for GitLab with token."""
        url = _build_clone_url("owner/repo", GitProvider.GITLAB, "gltoken123")
        assert url == "https://oauth2:gltoken123@gitlab.com/owner/repo.git"

    def test_build_clone_url_gitlab_https_with_token(self):
        """Test building clone URL from GitLab URL with token."""
        url = _build_clone_url(
            "https://gitlab.com/owner/repo", GitProvider.GITLAB, "gltoken123"
        )
        assert url == "https://oauth2:gltoken123@gitlab.com/owner/repo"

    def test_build_clone_url_bitbucket_with_token(self):
        """Test building clone URL for Bitbucket with token."""
        url = _build_clone_url("owner/repo", GitProvider.BITBUCKET, "bbtoken123")
        assert url == "https://x-token-auth:bbtoken123@bitbucket.org/owner/repo.git"

    def test_build_clone_url_no_token_passthrough(self):
        """Test that full URLs without token pass through unchanged."""
        url = _build_clone_url(
            "https://github.com/owner/repo", GitProvider.GITHUB, None
        )
        assert url == "https://github.com/owner/repo"


class TestGetReposContext:
    """Tests for get_repos_context function."""

    def test_empty_mappings(self):
        """Test that empty mappings return empty string."""
        assert get_repos_context({}) == ""

    def test_single_repo(self):
        """Test context generation for single repo."""
        mappings = {
            "owner/repo": RepoMapping(
                url="owner/repo",
                dir_name="repo",
                local_path="/workspace/project/repo",
                ref=None,
            )
        }
        context = get_repos_context(mappings)
        assert "## Cloned Repositories" in context
        assert "`owner/repo`" in context
        assert "`/workspace/project/repo/`" in context

    def test_repo_with_ref(self):
        """Test context generation for repo with ref."""
        mappings = {
            "owner/repo": RepoMapping(
                url="owner/repo",
                dir_name="repo",
                local_path="/workspace/project/repo",
                ref="main",
            )
        }
        context = get_repos_context(mappings)
        assert "(ref: main)" in context

    def test_multiple_repos(self):
        """Test context generation for multiple repos."""
        mappings = {
            "owner/repo1": RepoMapping(
                url="owner/repo1",
                dir_name="repo1",
                local_path="/workspace/project/repo1",
                ref=None,
            ),
            "owner/repo2": RepoMapping(
                url="owner/repo2",
                dir_name="repo2",
                local_path="/workspace/project/repo2",
                ref="v1.0",
            ),
        }
        context = get_repos_context(mappings)
        assert "`owner/repo1`" in context
        assert "`owner/repo2`" in context
        assert "(ref: v1.0)" in context


class TestCloneRepos:
    """Tests for clone_repos function."""

    def test_empty_repos_list(self):
        """Test cloning with empty repos list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = clone_repos([], Path(tmpdir))
            assert result.success_count == 0
            assert result.failed_repos == []
            assert result.repo_mappings == {}

    @patch("subprocess.run")
    def test_successful_clone(self, mock_run):
        """Test successful repo clone."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo", provider="github")]
            result = clone_repos(repos, Path(tmpdir))

            assert result.success_count == 1
            assert result.failed_repos == []
            assert "owner/repo" in result.repo_mappings
            assert result.repo_mappings["owner/repo"].dir_name == "repo"

    @patch("subprocess.run")
    def test_successful_clone_full_url(self, mock_run):
        """Test successful clone with full URL (no provider needed)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="https://github.com/owner/repo")]
            result = clone_repos(repos, Path(tmpdir))

            assert result.success_count == 1
            assert "https://github.com/owner/repo" in result.repo_mappings

    @patch("subprocess.run")
    def test_clone_with_sha_ref(self, mock_run):
        """Test clone with SHA ref (needs full clone + checkout)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo", ref="abc1234567", provider="github")]
            clone_repos(repos, Path(tmpdir))

            # Should have been called twice: clone + checkout
            assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_clone_failure(self, mock_run):
        """Test handling of clone failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Clone failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo", provider="github")]
            result = clone_repos(repos, Path(tmpdir))

            assert result.success_count == 0
            assert len(result.failed_repos) == 1
            assert result.repo_mappings == {}

    @patch("subprocess.run")
    def test_clone_with_token_fetcher(self, mock_run):
        """Test clone with token fetcher callback."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        def token_fetcher(name: str) -> str | None:
            if name == "github_token":
                return "ghtoken123"
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo", provider="github")]
            clone_repos(
                repos,
                Path(tmpdir),
                token_fetcher=token_fetcher,
            )

            # Check that token was included in clone URL
            call_args = mock_run.call_args[0][0]
            assert any("ghtoken123" in str(arg) for arg in call_args)

    @patch("subprocess.run")
    def test_clone_with_provider_specific_token(self, mock_run):
        """Test clone fetches correct token based on provider."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        fetched_tokens = []

        def token_fetcher(name: str) -> str | None:
            fetched_tokens.append(name)
            return f"token_for_{name}"

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [
                RepoSource(url="owner/repo1", provider="github"),
                RepoSource(url="owner/repo2", provider="gitlab"),
            ]
            clone_repos(repos, Path(tmpdir), token_fetcher=token_fetcher)

            # Should have fetched github_token and gitlab_token
            assert "github_token" in fetched_tokens
            assert "gitlab_token" in fetched_tokens

    @patch("subprocess.run")
    def test_directory_name_collision(self, mock_run):
        """Test handling of directory name collisions."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Two repos with same name should get unique directories
            repos = [
                RepoSource(url="owner1/utils", provider="github"),
                RepoSource(url="owner2/utils", provider="github"),
            ]
            result = clone_repos(repos, Path(tmpdir))

            dir_names = [m.dir_name for m in result.repo_mappings.values()]
            assert "utils" in dir_names
            assert "utils_1" in dir_names


class TestCloudWorkspaceRepoMethods:
    """Tests for OpenHandsCloudWorkspace repo methods."""

    @patch("openhands.workspace.cloud.workspace.clone_repos")
    @patch.object(
        __import__(
            "openhands.workspace.cloud.workspace", fromlist=["OpenHandsCloudWorkspace"]
        ).OpenHandsCloudWorkspace,
        "_get_secret_value",
        return_value=None,
    )
    def test_clone_repos_full_url_list(self, mock_secret, mock_clone):
        """Test clone_repos with list of full URL strings."""
        from openhands.workspace import OpenHandsCloudWorkspace

        mock_clone.return_value = CloneResult(0, [], {})

        with patch.object(
            OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
        ):
            workspace = OpenHandsCloudWorkspace(
                cloud_api_url="https://test.com",
                cloud_api_key="test-key",
                host="http://localhost:60000",
            )
            workspace._sandbox_id = "test-sandbox"
            workspace._session_api_key = "test-session"
            workspace.working_dir = "/workspace/project"

            # Full URLs don't need provider
            workspace.clone_repos(
                [
                    "https://github.com/owner/repo1",
                    "https://github.com/owner/repo2",
                ]
            )

            mock_clone.assert_called_once()
            call_args = mock_clone.call_args
            repos = call_args.kwargs["repos"]
            assert len(repos) == 2
            assert all(isinstance(r, RepoSource) for r in repos)

    @patch("openhands.workspace.cloud.workspace.clone_repos")
    @patch.object(
        __import__(
            "openhands.workspace.cloud.workspace", fromlist=["OpenHandsCloudWorkspace"]
        ).OpenHandsCloudWorkspace,
        "_get_secret_value",
        return_value=None,
    )
    def test_clone_repos_dict_list(self, mock_secret, mock_clone):
        """Test clone_repos with list of dicts."""
        from openhands.workspace import OpenHandsCloudWorkspace

        mock_clone.return_value = CloneResult(0, [], {})

        with patch.object(
            OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
        ):
            workspace = OpenHandsCloudWorkspace(
                cloud_api_url="https://test.com",
                cloud_api_key="test-key",
                host="http://localhost:60000",
            )
            workspace._sandbox_id = "test-sandbox"
            workspace._session_api_key = "test-session"
            workspace.working_dir = "/workspace/project"

            # Short URL with provider specified
            workspace.clone_repos(
                [{"url": "owner/repo", "ref": "main", "provider": "github"}]
            )

            mock_clone.assert_called_once()
            call_args = mock_clone.call_args
            repos = call_args.kwargs["repos"]
            assert len(repos) == 1
            assert repos[0].url == "owner/repo"
            assert repos[0].ref == "main"
            assert repos[0].provider == "github"

    def test_get_repos_context_from_mappings(self):
        """Test get_repos_context with explicit mappings."""
        from openhands.workspace import OpenHandsCloudWorkspace

        with patch.object(
            OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
        ):
            workspace = OpenHandsCloudWorkspace(
                cloud_api_url="https://test.com",
                cloud_api_key="test-key",
                host="http://localhost:60000",
            )
            workspace.working_dir = "/workspace/project"

            mappings = {
                "owner/repo": RepoMapping(
                    url="owner/repo",
                    dir_name="repo",
                    local_path="/workspace/project/repo",
                    ref="main",
                )
            }

            context = workspace.get_repos_context(mappings)
            assert "## Cloned Repositories" in context
            assert "`owner/repo`" in context


class TestCloneReposIntegration:
    """Integration tests for clone_repos using real git operations.

    These tests exercise actual git cloning behavior rather than mocking subprocess.
    Uses a small local git repository as a fixture to avoid network dependencies.
    """

    @pytest.fixture
    def local_git_repo(self, tmp_path):
        """Create a minimal local git repo for testing."""
        import subprocess

        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )

        # Create a file and commit
        (repo_dir / "README.md").write_text("# Test Repo")
        subprocess.run(
            ["git", "add", "README.md"], cwd=repo_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )

        # Create a tag
        subprocess.run(
            ["git", "tag", "v1.0.0"], cwd=repo_dir, capture_output=True, check=True
        )

        # Get the commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_sha = result.stdout.strip()

        return {"path": repo_dir, "sha": commit_sha}

    def test_clone_local_repo(self, local_git_repo, tmp_path):
        """Test cloning a local git repository."""
        target_dir = tmp_path / "cloned"
        repo_url = f"file://{local_git_repo['path']}"

        repos = [RepoSource(url=repo_url)]
        result = clone_repos(repos, target_dir)

        assert result.success_count == 1
        assert len(result.failed_repos) == 0
        assert repo_url in result.repo_mappings

        # Verify the repo was actually cloned
        cloned_path = Path(result.repo_mappings[repo_url].local_path)
        assert cloned_path.exists()
        assert (cloned_path / "README.md").exists()
        assert (cloned_path / "README.md").read_text() == "# Test Repo"

    def test_clone_with_tag_ref(self, local_git_repo, tmp_path):
        """Test cloning with a specific tag ref."""
        import subprocess

        target_dir = tmp_path / "cloned"
        repo_url = f"file://{local_git_repo['path']}"

        repos = [RepoSource(url=repo_url, ref="v1.0.0")]
        result = clone_repos(repos, target_dir)

        assert result.success_count == 1
        cloned_path = Path(result.repo_mappings[repo_url].local_path)
        assert cloned_path.exists()

        # Verify the tag was actually checked out
        tag_result = subprocess.run(
            ["git", "-C", str(cloned_path), "describe", "--tags", "--exact-match"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert tag_result.stdout.strip() == "v1.0.0"

    def test_clone_with_sha_ref(self, local_git_repo, tmp_path):
        """Test cloning with a specific commit SHA."""
        import subprocess

        target_dir = tmp_path / "cloned"
        repo_url = f"file://{local_git_repo['path']}"
        sha = local_git_repo["sha"]

        repos = [RepoSource(url=repo_url, ref=sha)]
        result = clone_repos(repos, target_dir)

        assert result.success_count == 1
        cloned_path = Path(result.repo_mappings[repo_url].local_path)
        assert cloned_path.exists()

        # Verify the SHA was actually checked out
        sha_result = subprocess.run(
            ["git", "-C", str(cloned_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert sha_result.stdout.strip() == sha

    def test_clone_invalid_url_fails(self, tmp_path):
        """Test that invalid URLs are handled gracefully."""
        target_dir = tmp_path / "cloned"

        repos = [RepoSource(url="file:///nonexistent/repo")]
        result = clone_repos(repos, target_dir)

        assert result.success_count == 0
        assert len(result.failed_repos) == 1

    def test_clone_duplicate_urls_deduplicated(self, local_git_repo, tmp_path):
        """Test that duplicate URLs are deduplicated."""
        target_dir = tmp_path / "cloned"
        repo_url = f"file://{local_git_repo['path']}"

        # Same URL twice
        repos = [RepoSource(url=repo_url), RepoSource(url=repo_url)]
        result = clone_repos(repos, target_dir)

        # Should only clone once
        assert result.success_count == 1
        assert len(result.repo_mappings) == 1
