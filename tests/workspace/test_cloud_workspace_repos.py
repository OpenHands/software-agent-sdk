"""Tests for repository cloning and skill loading in OpenHandsCloudWorkspace."""

import json
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

    def test_simple_url(self):
        """Test RepoSource with simple owner/repo URL."""
        repo = RepoSource(url="owner/repo")
        assert repo.url == "owner/repo"
        assert repo.ref is None

    def test_url_with_ref(self):
        """Test RepoSource with URL and ref."""
        repo = RepoSource(url="owner/repo", ref="main")
        assert repo.url == "owner/repo"
        assert repo.ref == "main"

    def test_full_https_url(self):
        """Test RepoSource with full HTTPS URL."""
        repo = RepoSource(url="https://github.com/owner/repo")
        assert repo.url == "https://github.com/owner/repo"

    def test_git_ssh_url(self):
        """Test RepoSource with git SSH URL."""
        repo = RepoSource(url="git@github.com:owner/repo.git")
        assert repo.url == "git@github.com:owner/repo.git"

    def test_string_normalization(self):
        """Test that string input is normalized to RepoSource."""
        repo = RepoSource.model_validate("owner/repo")
        assert repo.url == "owner/repo"
        assert repo.ref is None

    def test_dict_normalization(self):
        """Test that dict input is validated."""
        repo = RepoSource.model_validate({"url": "owner/repo", "ref": "v1.0"})
        assert repo.url == "owner/repo"
        assert repo.ref == "v1.0"

    def test_invalid_url_rejected(self):
        """Test that invalid URLs are rejected."""
        with pytest.raises(ValueError, match="URL must be"):
            RepoSource(url="invalid-url-format")

    def test_url_with_dots_allowed(self):
        """Test that URLs with dots in repo name are allowed."""
        repo = RepoSource(url="owner/repo.name")
        assert repo.url == "owner/repo.name"

    def test_url_with_dashes_allowed(self):
        """Test that URLs with dashes are allowed."""
        repo = RepoSource(url="my-org/my-repo")
        assert repo.url == "my-org/my-repo"

    def test_provider_explicit(self):
        """Test explicit provider specification."""
        repo = RepoSource(url="owner/repo", provider="gitlab")
        assert repo.provider == "gitlab"
        assert repo.get_provider() == GitProvider.GITLAB
        assert repo.get_token_name() == "gitlab_token"

    def test_provider_auto_detect_github(self):
        """Test auto-detection of GitHub provider."""
        repo = RepoSource(url="https://github.com/owner/repo")
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.GITHUB
        assert repo.get_token_name() == "github_token"

    def test_provider_auto_detect_gitlab(self):
        """Test auto-detection of GitLab provider."""
        repo = RepoSource(url="https://gitlab.com/owner/repo")
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.GITLAB
        assert repo.get_token_name() == "gitlab_token"

    def test_provider_auto_detect_bitbucket(self):
        """Test auto-detection of Bitbucket provider."""
        repo = RepoSource(url="https://bitbucket.org/owner/repo")
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.BITBUCKET
        assert repo.get_token_name() == "bitbucket_token"

    def test_provider_default_github(self):
        """Test that owner/repo format defaults to GitHub."""
        repo = RepoSource(url="owner/repo")
        assert repo.provider is None
        assert repo.get_provider() == GitProvider.GITHUB


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

    def test_detect_azure(self):
        assert (
            _detect_provider_from_url("https://dev.azure.com/o/p/_git/r")
            == GitProvider.AZURE
        )

    def test_detect_unknown(self):
        assert _detect_provider_from_url("https://example.com/o/r") is None
        assert _detect_provider_from_url("owner/repo") is None


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
            repos = [RepoSource(url="owner/repo")]
            result = clone_repos(repos, Path(tmpdir))

            assert result.success_count == 1
            assert result.failed_repos == []
            assert "owner/repo" in result.repo_mappings
            assert result.repo_mappings["owner/repo"].dir_name == "repo"

    @patch("subprocess.run")
    def test_clone_with_ref(self, mock_run):
        """Test clone with branch/tag ref."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo", ref="main")]
            clone_repos(repos, Path(tmpdir))

            # Check that --branch was included in command
            call_args = mock_run.call_args[0][0]
            assert "--branch" in call_args
            assert "main" in call_args

    @patch("subprocess.run")
    def test_clone_with_sha_ref(self, mock_run):
        """Test clone with SHA ref (needs full clone + checkout)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo", ref="abc1234567")]
            clone_repos(repos, Path(tmpdir))

            # Should have been called twice: clone + checkout
            assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_clone_failure(self, mock_run):
        """Test handling of clone failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Clone failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            repos = [RepoSource(url="owner/repo")]
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
            repos = [RepoSource(url="owner/repo")]
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
                RepoSource(url="owner/repo1"),  # GitHub (default)
                RepoSource(url="owner/repo2", provider="gitlab"),
            ]
            clone_repos(repos, Path(tmpdir), token_fetcher=token_fetcher)

            # Should have fetched github_token and gitlab_token
            assert "github_token" in fetched_tokens
            assert "gitlab_token" in fetched_tokens

    @patch("subprocess.run")
    def test_write_mapping_file(self, mock_run):
        """Test writing repos_mapping.json file."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            repos = [RepoSource(url="owner/repo")]
            mapping_file = tmppath / "repos_mapping.json"

            clone_repos(repos, tmppath, mapping_file=mapping_file)

            assert mapping_file.exists()
            with open(mapping_file) as f:
                data = json.load(f)
            assert "owner/repo" in data
            assert data["owner/repo"]["dir_name"] == "repo"

    @patch("subprocess.run")
    def test_directory_name_collision(self, mock_run):
        """Test handling of directory name collisions."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Two repos with same name should get unique directories
            repos = [
                RepoSource(url="owner1/utils"),
                RepoSource(url="owner2/utils"),
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
    def test_clone_repos_string_list(self, mock_secret, mock_clone):
        """Test clone_repos with list of URL strings."""
        from openhands.workspace import OpenHandsCloudWorkspace

        mock_clone.return_value = CloneResult(0, [], {})

        with patch.object(
            OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
        ):
            workspace = OpenHandsCloudWorkspace(
                cloud_api_url="https://test.com",
                cloud_api_key="test-key",
                local_agent_server_mode=True,
            )
            workspace._sandbox_id = "test-sandbox"
            workspace._session_api_key = "test-session"
            workspace.working_dir = "/workspace/project"

            workspace.clone_repos(["owner/repo1", "owner/repo2"])

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
                local_agent_server_mode=True,
            )
            workspace._sandbox_id = "test-sandbox"
            workspace._session_api_key = "test-session"
            workspace.working_dir = "/workspace/project"

            workspace.clone_repos([{"url": "owner/repo", "ref": "main"}])

            mock_clone.assert_called_once()
            call_args = mock_clone.call_args
            repos = call_args.kwargs["repos"]
            assert len(repos) == 1
            assert repos[0].url == "owner/repo"
            assert repos[0].ref == "main"

    def test_get_repos_context_from_mappings(self):
        """Test get_repos_context with explicit mappings."""
        from openhands.workspace import OpenHandsCloudWorkspace

        with patch.object(
            OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
        ):
            workspace = OpenHandsCloudWorkspace(
                cloud_api_url="https://test.com",
                cloud_api_key="test-key",
                local_agent_server_mode=True,
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

    def test_get_repos_context_from_file(self):
        """Test get_repos_context reading from repos_mapping.json."""
        from openhands.workspace import OpenHandsCloudWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mapping file
            mapping_file = Path(tmpdir) / "repos_mapping.json"
            mapping_data = {
                "owner/repo": {
                    "dir_name": "repo",
                    "local_path": f"{tmpdir}/repo",
                    "ref": "main",
                }
            }
            with open(mapping_file, "w") as f:
                json.dump(mapping_data, f)

            with patch.object(
                OpenHandsCloudWorkspace, "model_post_init", lambda self, ctx: None
            ):
                workspace = OpenHandsCloudWorkspace(
                    cloud_api_url="https://test.com",
                    cloud_api_key="test-key",
                    local_agent_server_mode=True,
                )
                workspace.working_dir = tmpdir

                context = workspace.get_repos_context()
                assert "## Cloned Repositories" in context
                assert "`owner/repo`" in context
