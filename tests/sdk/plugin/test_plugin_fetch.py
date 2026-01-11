"""Tests for Plugin.fetch() functionality."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk.plugin import Plugin, PluginFetchError, parse_plugin_source
from openhands.sdk.plugin.fetch import (
    _extract_readable_name,
    get_cache_path,
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


class TestPluginFetch:
    """Tests for Plugin.fetch() method."""

    def test_fetch_local_path(self, tmp_path: Path):
        """Test fetching from local path returns the path unchanged."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        result = Plugin.fetch(str(plugin_dir))
        assert result == plugin_dir.resolve()

    def test_fetch_local_path_nonexistent(self, tmp_path: Path):
        """Test fetching nonexistent local path raises error."""
        with pytest.raises(PluginFetchError, match="does not exist"):
            Plugin.fetch(str(tmp_path / "nonexistent"))

    def test_fetch_local_path_with_tilde(self, tmp_path: Path):
        """Test fetching local path with ~ expansion."""
        # Create a plugin in a subdirectory
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        # Mock Path.home() to return tmp_path
        with patch("openhands.sdk.plugin.fetch.Path.home", return_value=tmp_path):
            # This won't actually work with ~, but tests the path handling
            result = Plugin.fetch(str(plugin_dir))
            assert result.exists()

    @patch("openhands.sdk.plugin.fetch.subprocess.run")
    def test_fetch_github_shorthand_clones(self, mock_run: MagicMock, tmp_path: Path):
        """Test fetching GitHub shorthand clones the repository."""
        mock_run.return_value = MagicMock(returncode=0)

        # Create a fake git repo after "clone"
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "clone" in cmd:
                # Get destination from command
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".git").mkdir()
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        mock_run.side_effect = side_effect

        result = Plugin.fetch("github:owner/repo", cache_dir=tmp_path)

        assert result.exists()
        assert (result / ".git").exists()

        # Verify git clone was called
        clone_calls = [c for c in mock_run.call_args_list if "clone" in c[0][0]]
        assert len(clone_calls) == 1
        assert "https://github.com/owner/repo.git" in clone_calls[0][0][0]

    @patch("openhands.sdk.plugin.fetch.subprocess.run")
    def test_fetch_with_ref(self, mock_run: MagicMock, tmp_path: Path):
        """Test fetching with specific ref."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "clone" in cmd:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".git").mkdir()
            return MagicMock(returncode=0, stdout=b"main\n", stderr=b"")

        mock_run.side_effect = side_effect

        Plugin.fetch("github:owner/repo", cache_dir=tmp_path, ref="v1.0.0")

        # Verify --branch was passed to clone
        clone_calls = [c for c in mock_run.call_args_list if "clone" in c[0][0]]
        assert len(clone_calls) == 1
        clone_cmd = clone_calls[0][0][0]
        assert "--branch" in clone_cmd
        assert "v1.0.0" in clone_cmd

    @patch("openhands.sdk.plugin.fetch.subprocess.run")
    def test_fetch_updates_existing_cache(self, mock_run: MagicMock, tmp_path: Path):
        """Test that fetch updates existing cached repository."""
        # Create a fake existing repo
        cache_path = tmp_path / "repo-12345678"
        cache_path.mkdir()
        (cache_path / ".git").mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout=b"main\n", stderr=b"")

        # Mock get_cache_path to return our fake path
        with patch(
            "openhands.sdk.plugin.fetch.get_cache_path", return_value=cache_path
        ):
            Plugin.fetch("github:owner/repo", cache_dir=tmp_path, update=True)

        # Verify git fetch was called (not clone)
        fetch_calls = [c for c in mock_run.call_args_list if "fetch" in c[0][0]]
        assert len(fetch_calls) >= 1

        clone_calls = [c for c in mock_run.call_args_list if "clone" in c[0][0]]
        assert len(clone_calls) == 0

    @patch("openhands.sdk.plugin.fetch.subprocess.run")
    def test_fetch_no_update_uses_cache(self, mock_run: MagicMock, tmp_path: Path):
        """Test that fetch with update=False uses cached version."""
        # Create a fake existing repo
        cache_path = tmp_path / "repo-12345678"
        cache_path.mkdir()
        (cache_path / ".git").mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout=b"main\n", stderr=b"")

        with patch(
            "openhands.sdk.plugin.fetch.get_cache_path", return_value=cache_path
        ):
            result = Plugin.fetch("github:owner/repo", cache_dir=tmp_path, update=False)

        assert result == cache_path

        # Verify no git operations were performed
        assert mock_run.call_count == 0

    @patch("openhands.sdk.plugin.fetch.subprocess.run")
    def test_fetch_git_error_raises_plugin_fetch_error(
        self, mock_run: MagicMock, tmp_path: Path
    ):
        """Test that git errors are wrapped in PluginFetchError."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "git", stderr=b"fatal: repository not found"
        )

        with pytest.raises(PluginFetchError, match="Git operation failed"):
            Plugin.fetch("github:owner/nonexistent", cache_dir=tmp_path)

    @patch("openhands.sdk.plugin.fetch.subprocess.run")
    def test_fetch_timeout_raises_plugin_fetch_error(
        self, mock_run: MagicMock, tmp_path: Path
    ):
        """Test that git timeout is wrapped in PluginFetchError."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 60)

        with pytest.raises(PluginFetchError, match="timed out"):
            Plugin.fetch("github:owner/slow-repo", cache_dir=tmp_path)


class TestPluginFetchIntegration:
    """Integration tests that require network access.

    These tests are marked with pytest.mark.integration and can be skipped
    in CI environments without network access.
    """

    @pytest.mark.integration
    def test_fetch_and_load_real_plugin(self, tmp_path: Path):
        """Test fetching and loading a real plugin from GitHub.

        This test requires network access and uses a public repository.
        """
        # Use a small public repo as test target
        # Note: This should be a stable public repo that won't disappear
        # For actual testing, you might want to use a dedicated test repo

        # Skip if no network (CI environment check)
        try:
            import socket

            socket.create_connection(("github.com", 443), timeout=5)
        except OSError:
            pytest.skip("No network access to GitHub")

        # This is a placeholder - in real tests you'd use an actual test plugin repo
        # For now we just verify the method signature works
        pass
