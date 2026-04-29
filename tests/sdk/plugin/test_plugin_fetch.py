"""Tests for plugin-specific fetch behavior.

Verifies that the plugin fetch layer correctly wraps extensions.fetch with
plugin-specific error types (PluginFetchError), the plugin DEFAULT_CACHE_DIR,
and the Plugin.fetch() classmethod.

Core fetch logic (parsing, caching, git operations) is tested in
tests/sdk/extensions/test_fetch.py.  Git infrastructure (clone, update,
checkout, locking) is tested in tests/sdk/git/test_cached_repo.py.
"""

from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest

from openhands.sdk.git.cached_repo import GitHelper
from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.plugin import Plugin, PluginFetchError
from openhands.sdk.plugin.fetch import fetch_plugin


def test_fetch_git_error_raises_plugin_fetch_error(tmp_path: Path):
    """ExtensionFetchError from git failures is wrapped as PluginFetchError."""
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = GitCommandError(
        "fatal: repository not found",
        command=["git", "clone"],
        exit_code=128,
    )

    with pytest.raises(PluginFetchError, match="Failed to fetch plugin"):
        fetch_plugin(
            "github:owner/nonexistent",
            cache_dir=tmp_path,
            git_helper=mock_git,
        )


def test_fetch_generic_error_raises_plugin_fetch_error(tmp_path: Path):
    """Generic runtime errors are also wrapped as PluginFetchError."""
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = RuntimeError("Unexpected error")

    with pytest.raises(PluginFetchError, match="Failed to fetch plugin"):
        fetch_plugin(
            "github:owner/repo",
            cache_dir=tmp_path,
            git_helper=mock_git,
        )


def test_fetch_local_with_repo_path_raises_plugin_fetch_error(
    tmp_path: Path,
):
    """repo_path rejection for local sources surfaces as PluginFetchError."""
    plugin_dir = tmp_path / "monorepo"
    plugin_dir.mkdir()

    with pytest.raises(PluginFetchError, match="repo_path is not supported for local"):
        fetch_plugin(str(plugin_dir), repo_path="plugins/my-plugin")


def test_fetch_uses_default_cache_dir(tmp_path: Path):
    """fetch_plugin uses the plugin-specific DEFAULT_CACHE_DIR."""
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect

    with patch("openhands.sdk.plugin.fetch.DEFAULT_CACHE_DIR", tmp_path / "cache"):
        result = fetch_plugin(
            "github:owner/repo",
            cache_dir=None,
            git_helper=mock_git,
        )

    assert result.exists()
    assert str(tmp_path / "cache") in str(result)


def test_plugin_fetch_delegates(tmp_path: Path):
    """Plugin.fetch() delegates to fetch_plugin for local paths."""
    plugin_dir = tmp_path / "my-plugin"
    plugin_dir.mkdir()

    result = Plugin.fetch(str(plugin_dir))
    assert result == plugin_dir.resolve()


def test_plugin_fetch_local_with_repo_path_raises_error(tmp_path: Path):
    """Plugin.fetch() raises PluginFetchError for local + repo_path."""
    plugin_dir = tmp_path / "monorepo"
    plugin_dir.mkdir()

    with pytest.raises(PluginFetchError, match="repo_path is not supported for local"):
        Plugin.fetch(str(plugin_dir), repo_path="plugins/my-plugin")
