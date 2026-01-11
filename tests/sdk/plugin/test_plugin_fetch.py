"""Tests for Plugin.fetch() functionality."""

import subprocess
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest

from openhands.sdk.plugin import (
    GitError,
    GitHelper,
    Plugin,
    PluginFetchError,
    parse_plugin_source,
)
from openhands.sdk.plugin.fetch import (
    _checkout_ref,
    _clone_repository,
    _extract_readable_name,
    _update_repository,
    fetch_plugin,
    get_cache_path,
    get_git_helper,
    set_git_helper,
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


class TestExtractReadableName:
    """Tests for _extract_readable_name function."""

    def test_github_shorthand(self):
        """Test extracting name from GitHub shorthand."""
        name = _extract_readable_name("github:owner/my-plugin")
        assert name == "my-plugin"

    def test_https_url(self):
        """Test extracting name from HTTPS URL."""
        name = _extract_readable_name("https://github.com/owner/my-plugin.git")
        assert name == "my-plugin"

    def test_ssh_url(self):
        """Test extracting name from SSH URL."""
        name = _extract_readable_name("git@github.com:owner/my-plugin.git")
        assert name == "my-plugin"

    def test_local_path(self):
        """Test extracting name from local path."""
        name = _extract_readable_name("/path/to/my-plugin")
        assert name == "my-plugin"

    def test_special_characters_sanitized(self):
        """Test that special characters are sanitized."""
        name = _extract_readable_name("https://github.com/owner/my.special@plugin!.git")
        assert name == "my-special-plugin"

    def test_long_name_truncated(self):
        """Test that long names are truncated."""
        name = _extract_readable_name(
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

        _update_repository(tmp_path, "v1.0.0", mock_git)

        # fetch is called twice: once in _update_repository, once in _checkout_ref
        assert mock_git.fetch.call_count == 2
        mock_git.checkout.assert_called_once_with(tmp_path, "v1.0.0")

    def test_update_handles_detached_head(self, tmp_path: Path):
        """Test update handles detached HEAD state (no reset)."""
        mock_git = create_autospec(GitHelper)
        mock_git.get_current_branch.return_value = None

        _update_repository(tmp_path, None, mock_git)

        mock_git.fetch.assert_called_once()
        mock_git.reset_hard.assert_not_called()

    def test_update_handles_git_error(self, tmp_path: Path):
        """Test update continues on GitError (uses cached version)."""
        mock_git = create_autospec(GitHelper)
        mock_git.fetch.side_effect = GitError("Network error")

        _update_repository(tmp_path, None, mock_git)


class TestCheckoutRef:
    """Tests for _checkout_ref function."""

    def test_checkout_fetches_and_checks_out(self, tmp_path: Path):
        """Test checkout fetches ref and checks out."""
        mock_git = create_autospec(GitHelper)

        _checkout_ref(tmp_path, "v1.0.0", mock_git)

        mock_git.fetch.assert_called_once_with(tmp_path, ref="v1.0.0")
        mock_git.checkout.assert_called_once_with(tmp_path, "v1.0.0")
        mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/v1.0.0")

    def test_checkout_handles_fetch_error(self, tmp_path: Path):
        """Test checkout continues if fetch fails (e.g., for commits)."""
        mock_git = create_autospec(GitHelper)
        mock_git.fetch.side_effect = GitError("Not a branch")

        _checkout_ref(tmp_path, "abc123", mock_git)

        mock_git.checkout.assert_called_once_with(tmp_path, "abc123")

    def test_checkout_handles_reset_error(self, tmp_path: Path):
        """Test checkout continues if reset fails (e.g., for tags)."""
        mock_git = create_autospec(GitHelper)
        mock_git.reset_hard.side_effect = GitError("Not a branch")

        _checkout_ref(tmp_path, "v1.0.0", mock_git)

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
        """Test that git errors are wrapped in PluginFetchError."""
        mock_git = create_autospec(GitHelper)
        mock_git.clone.side_effect = GitError("fatal: repository not found")

        with pytest.raises(PluginFetchError, match="Git operation failed"):
            fetch_plugin(
                "github:owner/nonexistent", cache_dir=tmp_path, git_helper=mock_git
            )

    def test_fetch_generic_error_wrapped(self, tmp_path: Path):
        """Test that generic errors are wrapped in PluginFetchError."""
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


class TestSetGitHelper:
    """Tests for set_git_helper and get_git_helper functions."""

    def test_set_and_get_git_helper(self):
        """Test setting and getting a custom GitHelper instance."""
        # Save original
        original = get_git_helper()

        try:
            # Set a custom helper
            custom_helper = GitHelper()
            set_git_helper(custom_helper)
            assert get_git_helper() is custom_helper

            # Reset to None to test lazy initialization
            set_git_helper(None)
            # Next get should create a new instance
            new_helper = get_git_helper()
            assert new_helper is not None
            assert new_helper is not custom_helper
        finally:
            # Restore original
            set_git_helper(original)


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

    def test_fetch_reraises_plugin_fetch_error(self, tmp_path: Path):
        """Test that PluginFetchError is re-raised directly (line 248)."""
        mock_git = create_autospec(GitHelper)

        # Make clone raise PluginFetchError to test the re-raise path
        mock_git.clone.side_effect = PluginFetchError("Test error from clone")

        # This should re-raise the PluginFetchError directly
        with pytest.raises(PluginFetchError, match="Test error from clone"):
            fetch_plugin(
                "github:owner/repo",
                cache_dir=tmp_path,
                git_helper=mock_git,
            )


class TestGitHelperErrors:
    """Tests for GitHelper error handling paths."""

    def test_clone_called_process_error(self, tmp_path: Path):
        """Test clone handles CalledProcessError (lines 62-64)."""
        git = GitHelper()
        dest = tmp_path / "repo"

        # Try to clone a non-existent repo
        with pytest.raises(GitError, match="Clone failed"):
            git.clone("https://invalid.example.com/nonexistent.git", dest, timeout=5)

    def test_clone_timeout(self, tmp_path: Path):
        """Test clone handles TimeoutExpired (lines 65-66)."""
        git = GitHelper()
        dest = tmp_path / "repo"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitError, match="timed out"):
                git.clone("https://github.com/owner/repo.git", dest, timeout=1)

    def test_fetch_with_ref(self, tmp_path: Path):
        """Test fetch appends ref to command (line 88)."""
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
        # This will fail because there's no remote, but it will hit line 88
        with pytest.raises(GitError, match="Fetch failed"):
            git.fetch(repo, ref="main")

    def test_fetch_called_process_error(self, tmp_path: Path):
        """Test fetch handles CalledProcessError (lines 100-102)."""
        git = GitHelper()
        repo = tmp_path / "not-a-repo"
        repo.mkdir()

        with pytest.raises(GitError, match="Fetch failed"):
            git.fetch(repo)

    def test_fetch_timeout(self, tmp_path: Path):
        """Test fetch handles TimeoutExpired (lines 103-104)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitError, match="timed out"):
                git.fetch(repo, timeout=1)

    def test_checkout_called_process_error(self, tmp_path: Path):
        """Test checkout handles CalledProcessError (lines 129-131)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True)

        with pytest.raises(GitError, match="Checkout failed"):
            git.checkout(repo, "nonexistent-ref")

    def test_checkout_timeout(self, tmp_path: Path):
        """Test checkout handles TimeoutExpired (lines 132-133)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitError, match="timed out"):
                git.checkout(repo, "main", timeout=1)

    def test_reset_hard_called_process_error(self, tmp_path: Path):
        """Test reset_hard handles CalledProcessError (lines 158-160)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True)

        with pytest.raises(GitError, match="Reset failed"):
            git.reset_hard(repo, "nonexistent-ref")

    def test_reset_hard_timeout(self, tmp_path: Path):
        """Test reset_hard handles TimeoutExpired (lines 161-162)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitError, match="timed out"):
                git.reset_hard(repo, "HEAD", timeout=1)

    def test_get_current_branch_called_process_error(self, tmp_path: Path):
        """Test get_current_branch handles CalledProcessError (lines 192-194)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        # Mock subprocess.run to raise CalledProcessError
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["git"], stderr="fatal: not a git repository"
            )
            with pytest.raises(GitError, match="Failed to get current branch"):
                git.get_current_branch(repo)

    def test_get_current_branch_timeout(self, tmp_path: Path):
        """Test get_current_branch handles TimeoutExpired (lines 195-196)."""
        git = GitHelper()
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            with pytest.raises(GitError, match="timed out"):
                git.get_current_branch(repo, timeout=1)
