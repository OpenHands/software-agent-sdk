"""Tests for Plugin.fetch() functionality."""

import subprocess
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest

from openhands.sdk.git.cached_repo import (
    GitHelper,
    _checkout_ref,
    _clone_repository,
    _update_repository,
)
from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.git.utils import extract_repo_name
from openhands.sdk.plugin import (
    Plugin,
    PluginFetchError,
)
from openhands.sdk.plugin.fetch import (
    fetch_plugin,
    get_cache_path,
    parse_plugin_source,
)


class TestParsePluginSource:
    """Tests for parse_plugin_source function."""

    def test_github_shorthand(self):
        """Test parsing GitHub shorthand format."""
        source_type, url = parse_plugin_source("github:owner/repo")
        assert source_type == "github"
        assert url == "https://github.com/owner/repo.git"

    def test_github_shorthand_with_whitespace(self):
        """Test parsing GitHub shorthand with leading/trailing whitespace."""
        source_type, url = parse_plugin_source("  github:owner/repo  ")
        assert source_type == "github"
        assert url == "https://github.com/owner/repo.git"

    def test_github_shorthand_invalid_format(self):
        """Test that invalid GitHub shorthand raises error."""
        with pytest.raises(PluginFetchError, match="Invalid GitHub shorthand"):
            parse_plugin_source("github:invalid")

        with pytest.raises(PluginFetchError, match="Invalid GitHub shorthand"):
            parse_plugin_source("github:too/many/parts")

    def test_https_git_url(self):
        """Test parsing HTTPS git URLs."""
        source_type, url = parse_plugin_source("https://github.com/owner/repo.git")
        assert source_type == "git"
        assert url == "https://github.com/owner/repo.git"

    def test_https_github_url_without_git_suffix(self):
        """Test parsing GitHub HTTPS URL without .git suffix."""
        source_type, url = parse_plugin_source("https://github.com/owner/repo")
        assert source_type == "git"
        assert url == "https://github.com/owner/repo.git"

    def test_https_github_url_with_trailing_slash(self):
        """Test parsing GitHub HTTPS URL with trailing slash."""
        source_type, url = parse_plugin_source("https://github.com/owner/repo/")
        assert source_type == "git"
        assert url == "https://github.com/owner/repo.git"

    def test_https_gitlab_url(self):
        """Test parsing GitLab HTTPS URLs."""
        source_type, url = parse_plugin_source("https://gitlab.com/org/repo")
        assert source_type == "git"
        assert url == "https://gitlab.com/org/repo.git"

    def test_https_bitbucket_url(self):
        """Test parsing Bitbucket HTTPS URLs."""
        source_type, url = parse_plugin_source("https://bitbucket.org/org/repo")
        assert source_type == "git"
        assert url == "https://bitbucket.org/org/repo.git"

    def test_ssh_git_url(self):
        """Test parsing SSH git URLs."""
        source_type, url = parse_plugin_source("git@github.com:owner/repo.git")
        assert source_type == "git"
        assert url == "git@github.com:owner/repo.git"

    def test_git_protocol_url(self):
        """Test parsing git:// protocol URLs."""
        source_type, url = parse_plugin_source("git://github.com/owner/repo.git")
        assert source_type == "git"
        assert url == "git://github.com/owner/repo.git"

    def test_absolute_local_path(self):
        """Test parsing absolute local paths."""
        source_type, url = parse_plugin_source("/path/to/plugin")
        assert source_type == "local"
        assert url == "/path/to/plugin"

    def test_home_relative_path(self):
        """Test parsing home-relative paths."""
        source_type, url = parse_plugin_source("~/plugins/my-plugin")
        assert source_type == "local"
        assert url == "~/plugins/my-plugin"

    def test_relative_path(self):
        """Test parsing relative paths."""
        source_type, url = parse_plugin_source("./plugins/my-plugin")
        assert source_type == "local"
        assert url == "./plugins/my-plugin"

    def test_invalid_source(self):
        """Test that unparseable sources raise error."""
        with pytest.raises(PluginFetchError, match="Unable to parse plugin source"):
            parse_plugin_source("invalid-source-format")

    def test_self_hosted_git_url(self):
        """Test parsing URLs from self-hosted/alternative git providers."""
        # Codeberg
        source_type, url = parse_plugin_source("https://codeberg.org/user/repo")
        assert source_type == "git"
        assert url == "https://codeberg.org/user/repo.git"

        # Self-hosted GitLab
        source_type, url = parse_plugin_source("https://git.mycompany.com/org/repo")
        assert source_type == "git"
        assert url == "https://git.mycompany.com/org/repo.git"

        # SourceHut
        source_type, url = parse_plugin_source("https://sr.ht/~user/repo")
        assert source_type == "git"
        assert url == "https://sr.ht/~user/repo.git"

    def test_http_url(self):
        """Test parsing plain HTTP URLs (some internal servers)."""
        source_type, url = parse_plugin_source("http://internal-git.local/repo")
        assert source_type == "git"
        assert url == "http://internal-git.local/repo.git"

    def test_ssh_with_custom_user(self):
        """Test SSH URLs with non-git usernames."""
        ssh_url = "deploy@git.example.com:project/repo.git"
        source_type, url = parse_plugin_source(ssh_url)
        assert source_type == "git"
        assert url == ssh_url


class TestExtractRepoName:
    """Tests for extract_repo_name function (in git.utils)."""

    def test_github_shorthand(self):
        """Test extracting name from GitHub shorthand."""
        name = extract_repo_name("github:owner/my-plugin")
        assert name == "my-plugin"

    def test_https_url(self):
        """Test extracting name from HTTPS URL."""
        name = extract_repo_name("https://github.com/owner/my-plugin.git")
        assert name == "my-plugin"

    def test_ssh_url(self):
        """Test extracting name from SSH URL."""
        name = extract_repo_name("git@github.com:owner/my-plugin.git")
        assert name == "my-plugin"

    def test_local_path(self):
        """Test extracting name from local path."""
        name = extract_repo_name("/path/to/my-plugin")
        assert name == "my-plugin"

    def test_special_characters_sanitized(self):
        """Test that special characters are sanitized."""
        name = extract_repo_name("https://github.com/owner/my.special@plugin!.git")
        assert name == "my-special-plugin"

    def test_long_name_truncated(self):
        """Test that long names are truncated."""
        name = extract_repo_name(
            "github:owner/this-is-a-very-long-plugin-name-that-should-be-truncated"
        )
        assert len(name) <= 32


class TestGetCachePath:
    """Tests for get_cache_path function."""

    def test_deterministic_path(self, tmp_path: Path):
        """Test that cache path is deterministic for same source."""
        source = "https://github.com/owner/repo.git"
        path1 = get_cache_path(source, tmp_path)
        path2 = get_cache_path(source, tmp_path)
        assert path1 == path2

    def test_different_sources_different_paths(self, tmp_path: Path):
        """Test that different sources get different paths."""
        path1 = get_cache_path("https://github.com/owner/repo1.git", tmp_path)
        path2 = get_cache_path("https://github.com/owner/repo2.git", tmp_path)
        assert path1 != path2

    def test_path_includes_readable_name(self, tmp_path: Path):
        """Test that cache path includes readable name."""
        source = "https://github.com/owner/my-plugin.git"
        path = get_cache_path(source, tmp_path)
        assert "my-plugin" in path.name

    def test_default_cache_dir(self):
        """Test that default cache dir is under ~/.openhands/cache/plugins/."""
        source = "https://github.com/owner/repo.git"
        path = get_cache_path(source)
        assert ".openhands" in str(path)
        assert "cache" in str(path)
        assert "plugins" in str(path)


class TestCloneRepository:
    """Tests for _clone_repository function."""

    def test_clone_calls_git_helper(self, tmp_path: Path):
        """Test that clone delegates to GitHelper."""
        mock_git = create_autospec(GitHelper)
        dest = tmp_path / "repo"

        _clone_repository("https://github.com/owner/repo.git", dest, None, mock_git)

        mock_git.clone.assert_called_once_with(
            "https://github.com/owner/repo.git", dest, depth=1, branch=None
        )

    def test_clone_with_ref(self, tmp_path: Path):
        """Test clone with branch/tag ref."""
        mock_git = create_autospec(GitHelper)
        dest = tmp_path / "repo"

        _clone_repository("https://github.com/owner/repo.git", dest, "v1.0.0", mock_git)

        mock_git.clone.assert_called_once_with(
            "https://github.com/owner/repo.git", dest, depth=1, branch="v1.0.0"
        )

    def test_clone_removes_existing_directory(self, tmp_path: Path):
        """Test that existing non-git directory is removed."""
        mock_git = create_autospec(GitHelper)
        dest = tmp_path / "repo"
        dest.mkdir()
        (dest / "some_file.txt").write_text("test")

        _clone_repository("https://github.com/owner/repo.git", dest, None, mock_git)

        mock_git.clone.assert_called_once()


class TestUpdateRepository:
    """Tests for _update_repository function."""

    def test_update_fetches_and_resets(self, tmp_path: Path):
        """Test update fetches from origin and resets to branch."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = "main"

        _update_repository(tmp_path, None, mock_git)

        mock_git.fetch.assert_called_once_with(tmp_path)
        mock_git.get_current_branch.assert_called_once_with(tmp_path)
        mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/main")

    def test_update_with_ref_checks_out(self, tmp_path: Path):
        """Test update with ref checks out that ref."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = None  # Assume tag/commit (detached)

        _update_repository(tmp_path, "v1.0.0", mock_git)

        # fetch is called once in _update_repository (checkout_ref no longer fetches)
        mock_git.fetch.assert_called_once_with(tmp_path)
        mock_git.checkout.assert_called_once_with(tmp_path, "v1.0.0")

    def test_update_detached_head_recovers_to_default_branch(self, tmp_path: Path):
        """Test update recovers from detached HEAD by checking out default branch.

        When a repo is in detached HEAD state (e.g., from a previous checkout of a
        tag) and update is called without a ref, it should:
        1. Detect the detached HEAD state
        2. Determine the remote's default branch via origin/HEAD
        3. Checkout and reset to that default branch

        This ensures that `fetch(source, update=True)` without a ref means "get the
        latest from the default branch", not "stay stuck on whatever was previously
        checked out".
        """
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = None  # Detached HEAD
        mock_git.get_default_branch.return_value = "main"

        _update_repository(tmp_path, None, mock_git)

        mock_git.fetch.assert_called_once()
        mock_git.get_current_branch.assert_called_once()
        mock_git.get_default_branch.assert_called_once_with(tmp_path)
        mock_git.checkout.assert_called_once_with(tmp_path, "main")
        mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/main")

    def test_update_detached_head_no_default_branch_logs_warning(self, tmp_path: Path):
        """Test update logs warning when detached HEAD and default branch unknown.

        If origin/HEAD is not set (can happen with some git configurations), we
        can't determine the default branch. In this case, log a warning and use
        the cached version as-is.
        """
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = None  # Detached HEAD
        mock_git.get_default_branch.return_value = None  # Can't determine default

        _update_repository(tmp_path, None, mock_git)

        mock_git.fetch.assert_called_once()
        mock_git.get_default_branch.assert_called_once()
        # Should not attempt checkout since we don't know the target
        mock_git.checkout.assert_not_called()
        mock_git.reset_hard.assert_not_called()

    def test_update_continues_on_fetch_error(self, tmp_path: Path):
        """Test update logs warning on fetch failure but doesn't raise."""
        mock_git = create_autospec(GitHelper)
        mock_git.fetch.side_effect = GitCommandError(
            "Network error", command=["git", "fetch"], exit_code=1
        )

        # Should not raise - graceful degradation
        _update_repository(tmp_path, None, mock_git)

        # Fetch was attempted
        mock_git.fetch.assert_called_once()
        # No further operations since fetch failed
        mock_git.get_current_branch.assert_not_called()

    def test_update_continues_on_checkout_error(self, tmp_path: Path):
        """Test update logs warning on checkout failure but doesn't raise."""
        mock_git = create_autospec(GitHelper)
        mock_git.checkout.side_effect = GitCommandError(
            "Invalid ref", command=["git", "checkout"], exit_code=1
        )

        # Should not raise - graceful degradation
        _update_repository(tmp_path, "nonexistent", mock_git)


class TestCheckoutRef:
    """Tests for _checkout_ref function.

    The function detects ref type AFTER checkout by checking HEAD state:
    - Detached HEAD (None from get_current_branch) = tag or commit
    - On a branch = reset to origin/{branch}
    """

    def test_checkout_branch_resets_to_origin(self, tmp_path: Path):
        """Test checkout of a branch resets to origin."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = "main"  # On a branch

        _checkout_ref(tmp_path, "main", mock_git)

        mock_git.checkout.assert_called_once_with(tmp_path, "main")
        mock_git.get_current_branch.assert_called_once_with(tmp_path)
        mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/main")

    def test_checkout_tag_skips_reset(self, tmp_path: Path):
        """Test checkout of a tag (detached HEAD) skips reset."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = None  # Detached HEAD

        _checkout_ref(tmp_path, "v1.0.0", mock_git)

        mock_git.checkout.assert_called_once_with(tmp_path, "v1.0.0")
        mock_git.get_current_branch.assert_called_once_with(tmp_path)
        mock_git.reset_hard.assert_not_called()

    def test_checkout_commit_skips_reset(self, tmp_path: Path):
        """Test checkout of a commit SHA (detached HEAD) skips reset."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = None  # Detached HEAD

        _checkout_ref(tmp_path, "abc123def", mock_git)

        mock_git.checkout.assert_called_once_with(tmp_path, "abc123def")
        mock_git.reset_hard.assert_not_called()

    def test_checkout_branch_handles_reset_error(self, tmp_path: Path):
        """Test checkout continues if reset fails (e.g., branch not on remote)."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = "local-only"
        mock_git.reset_hard.side_effect = GitCommandError(
            "Not found", command=["git", "reset"], exit_code=1
        )

        # Should not raise - reset failure is logged but not fatal
        _checkout_ref(tmp_path, "local-only", mock_git)

        mock_git.checkout.assert_called_once()


class TestFetchPlugin:
    """Tests for fetch_plugin function."""

    def test_fetch_local_path(self, tmp_path: Path):
        """Test fetching from local path returns the path unchanged."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        result = fetch_plugin(str(plugin_dir))
        assert result == plugin_dir.resolve()

    def test_fetch_local_path_nonexistent(self, tmp_path: Path):
        """Test fetching nonexistent local path raises error."""
        with pytest.raises(PluginFetchError, match="does not exist"):
            fetch_plugin(str(tmp_path / "nonexistent"))

    def test_fetch_github_shorthand_clones(self, tmp_path: Path):
        """Test fetching GitHub shorthand clones the repository."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()

        mock_git.clone.side_effect = clone_side_effect

        result = fetch_plugin(
            "github:owner/repo", cache_dir=tmp_path, git_helper=mock_git
        )

        assert result.exists()
        mock_git.clone.assert_called_once()
        call_args = mock_git.clone.call_args
        assert call_args[0][0] == "https://github.com/owner/repo.git"

    def test_fetch_with_ref(self, tmp_path: Path):
        """Test fetching with specific ref."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()

        mock_git.clone.side_effect = clone_side_effect

        fetch_plugin(
            "github:owner/repo", cache_dir=tmp_path, ref="v1.0.0", git_helper=mock_git
        )

        mock_git.clone.assert_called_once()
        call_kwargs = mock_git.clone.call_args[1]
        assert call_kwargs["branch"] == "v1.0.0"

    def test_fetch_updates_existing_cache(self, tmp_path: Path):
        """Test that fetch updates existing cached repository."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = "main"

        cache_path = get_cache_path("https://github.com/owner/repo.git", tmp_path)
        cache_path.mkdir(parents=True)
        (cache_path / ".git").mkdir()

        result = fetch_plugin(
            "github:owner/repo", cache_dir=tmp_path, update=True, git_helper=mock_git
        )

        assert result == cache_path
        mock_git.fetch.assert_called()
        mock_git.clone.assert_not_called()

    def test_fetch_no_update_uses_cache(self, tmp_path: Path):
        """Test that fetch with update=False uses cached version."""
        mock_git = create_autospec(GitHelper)

        cache_path = get_cache_path("https://github.com/owner/repo.git", tmp_path)
        cache_path.mkdir(parents=True)
        (cache_path / ".git").mkdir()

        result = fetch_plugin(
            "github:owner/repo", cache_dir=tmp_path, update=False, git_helper=mock_git
        )

        assert result == cache_path
        mock_git.clone.assert_not_called()
        mock_git.fetch.assert_not_called()

    def test_fetch_no_update_with_ref_checks_out(self, tmp_path: Path):
        """Test that fetch with update=False but ref still checks out."""
        mock_git = create_autospec(GitHelper)

        cache_path = get_cache_path("https://github.com/owner/repo.git", tmp_path)
        cache_path.mkdir(parents=True)
        (cache_path / ".git").mkdir()

        fetch_plugin(
            "github:owner/repo",
            cache_dir=tmp_path,
            update=False,
            ref="v1.0.0",
            git_helper=mock_git,
        )

        mock_git.checkout.assert_called_once_with(cache_path, "v1.0.0")

    def test_fetch_git_error_raises_plugin_fetch_error(self, tmp_path: Path):
        """Test that git errors result in PluginFetchError."""
        mock_git = create_autospec(GitHelper)
        mock_git.clone.side_effect = GitCommandError(
            "fatal: repository not found", command=["git", "clone"], exit_code=128
        )

        with pytest.raises(PluginFetchError, match="Failed to fetch plugin"):
            fetch_plugin(
                "github:owner/nonexistent", cache_dir=tmp_path, git_helper=mock_git
            )

    def test_fetch_generic_error_raises_plugin_fetch_error(self, tmp_path: Path):
        """Test that generic errors result in PluginFetchError."""
        mock_git = create_autospec(GitHelper)
        mock_git.clone.side_effect = RuntimeError("Unexpected error")

        with pytest.raises(PluginFetchError, match="Failed to fetch plugin"):
            fetch_plugin("github:owner/repo", cache_dir=tmp_path, git_helper=mock_git)


class TestPluginFetchMethod:
    """Tests for Plugin.fetch() classmethod."""

    def test_fetch_delegates_to_fetch_plugin(self, tmp_path: Path):
        """Test that Plugin.fetch() delegates to fetch_plugin."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        result = Plugin.fetch(str(plugin_dir))
        assert result == plugin_dir.resolve()

    def test_fetch_local_path_with_tilde(self, tmp_path: Path):
        """Test fetching local path with ~ expansion."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        with patch("openhands.sdk.plugin.fetch.Path.home", return_value=tmp_path):
            result = Plugin.fetch(str(plugin_dir))
            assert result.exists()


class TestRepoPathParameter:
    """Tests for repo_path parameter in fetch_plugin() and Plugin.fetch()."""

    def test_fetch_local_path_with_repo_path_raises_error(self, tmp_path: Path):
        """Test that repo_path is not supported for local sources."""
        plugin_dir = tmp_path / "monorepo"
        plugin_dir.mkdir()
        subplugin_dir = plugin_dir / "plugins" / "my-plugin"
        subplugin_dir.mkdir(parents=True)

        with pytest.raises(
            PluginFetchError, match="repo_path is not supported for local"
        ):
            fetch_plugin(str(plugin_dir), repo_path="plugins/my-plugin")

    def test_fetch_local_path_without_repo_path(self, tmp_path: Path):
        """Test fetching local path works without repo_path."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        result = fetch_plugin(str(plugin_dir))
        assert result == plugin_dir.resolve()

    def test_fetch_github_with_repo_path(self, tmp_path: Path):
        """Test fetching from GitHub with repo_path returns subdirectory."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            # Create the subdirectory structure
            subdir = dest / "plugins" / "sub-plugin"
            subdir.mkdir(parents=True)

        mock_git.clone.side_effect = clone_side_effect

        result = fetch_plugin(
            "github:owner/monorepo",
            cache_dir=tmp_path,
            repo_path="plugins/sub-plugin",
            git_helper=mock_git,
        )

        assert result.exists()
        assert result.name == "sub-plugin"
        assert "plugins" in str(result)

    def test_fetch_github_with_nonexistent_repo_path(self, tmp_path: Path):
        """Test fetching from GitHub with nonexistent repo_path raises error."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()

        mock_git.clone.side_effect = clone_side_effect

        with pytest.raises(PluginFetchError, match="Subdirectory.*not found"):
            fetch_plugin(
                "github:owner/repo",
                cache_dir=tmp_path,
                repo_path="nonexistent",
                git_helper=mock_git,
            )

    def test_fetch_with_repo_path_and_ref(self, tmp_path: Path):
        """Test fetching with both repo_path and ref."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            subdir = dest / "plugins" / "my-plugin"
            subdir.mkdir(parents=True)

        mock_git.clone.side_effect = clone_side_effect

        result = fetch_plugin(
            "github:owner/monorepo",
            cache_dir=tmp_path,
            ref="v1.0.0",
            repo_path="plugins/my-plugin",
            git_helper=mock_git,
        )

        assert result.exists()
        mock_git.clone.assert_called_once()
        call_kwargs = mock_git.clone.call_args[1]
        assert call_kwargs["branch"] == "v1.0.0"

    def test_plugin_fetch_local_with_repo_path_raises_error(self, tmp_path: Path):
        """Test Plugin.fetch() raises error for local source with repo_path."""
        plugin_dir = tmp_path / "monorepo"
        plugin_dir.mkdir()

        with pytest.raises(
            PluginFetchError, match="repo_path is not supported for local"
        ):
            Plugin.fetch(str(plugin_dir), repo_path="plugins/my-plugin")

    def test_fetch_no_repo_path_returns_root(self, tmp_path: Path):
        """Test that fetch without repo_path returns repository root."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            (dest / "plugins").mkdir()

        mock_git.clone.side_effect = clone_side_effect

        result = fetch_plugin(
            "github:owner/repo",
            cache_dir=tmp_path,
            repo_path=None,
            git_helper=mock_git,
        )

        assert result.exists()
        assert (result / ".git").exists()


class TestParsePluginSourceEdgeCases:
    """Additional edge case tests for parse_plugin_source."""

    def test_relative_path_with_slash(self):
        """Test parsing paths like 'plugins/my-plugin' (line 108)."""
        source_type, url = parse_plugin_source("plugins/my-plugin")
        assert source_type == "local"
        assert url == "plugins/my-plugin"

    def test_nested_relative_path(self):
        """Test parsing nested relative paths."""
        source_type, url = parse_plugin_source("path/to/my/plugin")
        assert source_type == "local"
        assert url == "path/to/my/plugin"


class TestFetchPluginEdgeCases:
    """Additional edge case tests for fetch_plugin."""

    def test_fetch_uses_default_cache_dir(self, tmp_path: Path):
        """Test fetch_plugin uses DEFAULT_CACHE_DIR when cache_dir is None."""
        mock_git = create_autospec(GitHelper)

        def clone_side_effect(url, dest, **kwargs):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()

        mock_git.clone.side_effect = clone_side_effect

        # Patch DEFAULT_CACHE_DIR to use tmp_path
        with patch("openhands.sdk.plugin.fetch.DEFAULT_CACHE_DIR", tmp_path / "cache"):
            result = fetch_plugin(
                "github:owner/repo",
                cache_dir=None,  # Explicitly None to trigger line 225
                git_helper=mock_git,
            )

        assert result.exists()
        assert str(tmp_path / "cache") in str(result)


class TestGitHelperErrors:
    """Tests for GitHelper error handling paths.

    These tests verify that GitHelper methods properly propagate GitCommandError
    from run_git_command when git operations fail.
    """

    def test_clone_called_process_error(self, tmp_path: Path):
        """Test clone raises GitCommandError on failure."""
        git = GitHelper()
        dest = tmp_path / "repo"

        # Try to clone a non-existent repo
        with pytest.raises(GitCommandError, match="git clone"):
            git.clone("https://invalid.example.com/nonexistent.git", dest, timeout=5)

    def test_clone_timeout(self, tmp_path: Path):
        """Test clone raises GitCommandError on timeout."""
        git = GitHelper()
        dest = tmp_path / "repo"

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitCommandError, match="timed out"):
                git.clone("https://github.com/owner/repo.git", dest, timeout=1)

    def test_fetch_with_ref(self, tmp_path: Path):
        """Test fetch with ref raises GitCommandError when no remote exists."""
        # Create a repo to fetch in
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo, check=True)

        git = GitHelper()
        # This will fail because there's no remote
        with pytest.raises(GitCommandError, match="git fetch"):
            git.fetch(repo, ref="main")

    def test_fetch_called_process_error(self, tmp_path: Path):
        """Test fetch raises GitCommandError on failure."""
        git = GitHelper()
        repo = tmp_path / "not-a-repo"
        repo.mkdir()

        with pytest.raises(GitCommandError, match="git fetch"):
            git.fetch(repo)

    def test_fetch_timeout(self, tmp_path: Path):
        """Test fetch raises GitCommandError on timeout."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitCommandError, match="timed out"):
                git.fetch(repo, timeout=1)

    def test_checkout_called_process_error(self, tmp_path: Path):
        """Test checkout raises GitCommandError on failure."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True)

        with pytest.raises(GitCommandError, match="git checkout"):
            git.checkout(repo, "nonexistent-ref")

    def test_checkout_timeout(self, tmp_path: Path):
        """Test checkout raises GitCommandError on timeout."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitCommandError, match="timed out"):
                git.checkout(repo, "main", timeout=1)

    def test_reset_hard_called_process_error(self, tmp_path: Path):
        """Test reset_hard raises GitCommandError on failure."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True)

        with pytest.raises(GitCommandError, match="git reset"):
            git.reset_hard(repo, "nonexistent-ref")

    def test_reset_hard_timeout(self, tmp_path: Path):
        """Test reset_hard raises GitCommandError on timeout."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitCommandError, match="timed out"):
                git.reset_hard(repo, "HEAD", timeout=1)

    def test_get_current_branch_called_process_error(self, tmp_path: Path):
        """Test get_current_branch raises GitCommandError on failure."""
        git = GitHelper()
        repo = tmp_path / "not-a-repo"
        repo.mkdir()

        with pytest.raises(GitCommandError, match="git rev-parse"):
            git.get_current_branch(repo)

    def test_get_current_branch_timeout(self, tmp_path: Path):
        """Test get_current_branch raises GitCommandError on timeout."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitCommandError, match="timed out"):
                git.get_current_branch(repo, timeout=1)


class TestGetDefaultBranch:
    """Tests for GitHelper.get_default_branch method."""

    def test_get_default_branch_returns_main(self, tmp_path: Path):
        """Test get_default_branch extracts branch name from origin/HEAD."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_result = subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="refs/remotes/origin/main\n",
                stderr="",
            )
            mock_run.return_value = mock_result

            result = git.get_default_branch(repo)

            assert result == "main"
            # Verify the correct command was called
            call_args = mock_run.call_args[0][0]
            assert call_args == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]

    def test_get_default_branch_returns_master(self, tmp_path: Path):
        """Test get_default_branch works with master as default branch."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_result = subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="refs/remotes/origin/master\n",
                stderr="",
            )
            mock_run.return_value = mock_result

            result = git.get_default_branch(repo)

            assert result == "master"

    def test_get_default_branch_returns_none_when_not_set(self, tmp_path: Path):
        """Test get_default_branch returns None when origin/HEAD is not set.

        This can happen with:
        - Bare clones
        - Repos where origin/HEAD was never configured
        - Some git server configurations
        """
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_result = subprocess.CompletedProcess(
                args=["git"],
                returncode=1,
                stdout="",
                stderr="fatal: ref refs/remotes/origin/HEAD is not a symbolic ref",
            )
            mock_run.return_value = mock_result

            result = git.get_default_branch(repo)

            assert result is None

    def test_get_default_branch_returns_none_on_unexpected_format(self, tmp_path: Path):
        """Test get_default_branch returns None for unexpected output format."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_result = subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="unexpected-format\n",  # Doesn't start with expected prefix
                stderr="",
            )
            mock_run.return_value = mock_result

            result = git.get_default_branch(repo)

            assert result is None


class TestCacheLocking:
    """Tests for cache directory locking behavior."""

    def test_lock_file_created_during_clone(self, tmp_path: Path):
        """Test that a lock file is created when cloning."""
        from openhands.sdk.git.cached_repo import try_cached_clone_or_update

        cache_dir = tmp_path / "cache"
        repo_path = cache_dir / "test-repo"

        mock_git = create_autospec(GitHelper, instance=True)

        # Track whether lock file exists during clone
        lock_existed_during_clone = []

        def mock_clone(url, dest, depth=None, branch=None, timeout=120):
            lock_path = repo_path.with_suffix(".lock")
            lock_existed_during_clone.append(lock_path.exists())

        mock_git.clone.side_effect = mock_clone

        try_cached_clone_or_update(
            url="https://github.com/test/repo.git",
            repo_path=repo_path,
            git_helper=mock_git,
        )

        # Lock file should have existed during the clone operation
        assert lock_existed_during_clone[0] is True

    def test_lock_timeout_returns_none(self, tmp_path: Path):
        """Test that lock timeout returns None gracefully."""
        from filelock import FileLock

        from openhands.sdk.git.cached_repo import try_cached_clone_or_update

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        repo_path = cache_dir / "test-repo"

        # Pre-acquire the lock to simulate another process holding it
        lock_path = repo_path.with_suffix(".lock")
        external_lock = FileLock(lock_path)
        external_lock.acquire()

        try:
            mock_git = create_autospec(GitHelper, instance=True)

            # Try to clone with a very short timeout
            result = try_cached_clone_or_update(
                url="https://github.com/test/repo.git",
                repo_path=repo_path,
                git_helper=mock_git,
                lock_timeout=0.1,  # Very short timeout
            )

            # Should return None due to lock timeout
            assert result is None
            # Clone should not have been called
            mock_git.clone.assert_not_called()
        finally:
            external_lock.release()

    def test_lock_released_after_operation(self, tmp_path: Path):
        """Test that lock is released after successful operation."""
        from filelock import FileLock

        from openhands.sdk.git.cached_repo import try_cached_clone_or_update

        cache_dir = tmp_path / "cache"
        repo_path = cache_dir / "test-repo"

        mock_git = create_autospec(GitHelper, instance=True)

        try_cached_clone_or_update(
            url="https://github.com/test/repo.git",
            repo_path=repo_path,
            git_helper=mock_git,
        )

        # Lock should be released - we should be able to acquire it immediately
        lock_path = repo_path.with_suffix(".lock")
        lock = FileLock(lock_path)
        lock.acquire(timeout=0)  # Should not block
        lock.release()

    def test_lock_released_on_error(self, tmp_path: Path):
        """Test that lock is released even when operation fails."""
        from filelock import FileLock

        from openhands.sdk.git.cached_repo import try_cached_clone_or_update

        cache_dir = tmp_path / "cache"
        repo_path = cache_dir / "test-repo"

        mock_git = create_autospec(GitHelper, instance=True)
        mock_git.clone.side_effect = GitCommandError(
            "Clone failed", command=["git", "clone"], exit_code=1, stderr="error"
        )

        result = try_cached_clone_or_update(
            url="https://github.com/test/repo.git",
            repo_path=repo_path,
            git_helper=mock_git,
        )

        assert result is None

        # Lock should still be released
        lock_path = repo_path.with_suffix(".lock")
        lock = FileLock(lock_path)
        lock.acquire(timeout=0)
        lock.release()
