"""Integration tests for Plugin.fetch() with real git operations.

These tests perform actual git operations and may require network access.
They are designed to test the full end-to-end flow of plugin fetching.
"""

import subprocess
from pathlib import Path

from openhands.sdk.git.cached_repo import GitHelper
from openhands.sdk.plugin import Plugin
from openhands.sdk.plugin.fetch import fetch_plugin


class TestGitHelperIntegration:
    """Integration tests for GitHelper with real git operations."""

    def test_clone_real_repo(self, tmp_path: Path):
        """Test cloning a real repository."""
        git = GitHelper()
        dest = tmp_path / "repo"

        # Create a local bare repo to clone from
        bare_repo = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True)

        git.clone(f"file://{bare_repo}", dest)

        assert dest.exists()
        assert (dest / ".git").exists()

    def test_clone_with_branch(self, tmp_path: Path):
        """Test cloning with a specific branch."""
        git = GitHelper()

        # Create a source repo with a branch
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init"], cwd=source, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=source, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
        (source / "file.txt").write_text("content")
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=source, check=True)
        subprocess.run(["git", "branch", "feature"], cwd=source, check=True)

        dest = tmp_path / "dest"
        git.clone(f"file://{source}", dest, branch="feature")

        assert dest.exists()
        # Verify we're on the feature branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=dest,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "feature"

    def test_fetch_and_checkout(self, tmp_path: Path):
        """Test fetch and checkout operations."""
        git = GitHelper()

        # Create source repo
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init"], cwd=source, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=source, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
        (source / "file.txt").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=source, check=True)
        subprocess.run(["git", "tag", "v1.0.0"], cwd=source, check=True)

        # Clone it
        dest = tmp_path / "dest"
        git.clone(f"file://{source}", dest, depth=None)

        # Make changes in source
        (source / "file.txt").write_text("v2")
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=source, check=True)

        # Fetch and verify
        git.fetch(dest)

        # Checkout tag
        git.checkout(dest, "v1.0.0")
        assert (dest / "file.txt").read_text() == "v1"

    def test_get_current_branch(self, tmp_path: Path):
        """Test getting current branch name."""
        git = GitHelper()

        # Create repo
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

        # Default branch
        branch = git.get_current_branch(repo)
        assert branch in ("main", "master")

        # Create and switch to new branch
        subprocess.run(["git", "checkout", "-b", "develop"], cwd=repo, check=True)
        branch = git.get_current_branch(repo)
        assert branch == "develop"

    def test_get_current_branch_detached_head(self, tmp_path: Path):
        """Test that detached HEAD returns None."""
        git = GitHelper()

        # Create repo with commits
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "file.txt").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=repo, check=True)
        (repo / "file.txt").write_text("v2")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=repo, check=True)

        # Get commit hash of first commit
        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        first_commit = result.stdout.strip()

        # Detach HEAD
        subprocess.run(["git", "checkout", first_commit], cwd=repo, check=True)

        branch = git.get_current_branch(repo)
        assert branch is None


class TestFetchPluginIntegration:
    """Integration tests for fetch_plugin with real git operations."""

    def test_fetch_from_local_git_repo(self, tmp_path: Path):
        """Test fetching a plugin from a local git repository."""
        # Create a plugin repo
        plugin_repo = tmp_path / "my-plugin"
        plugin_repo.mkdir()
        subprocess.run(["git", "init"], cwd=plugin_repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=plugin_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=plugin_repo, check=True
        )

        # Add plugin files
        (plugin_repo / ".plugin").mkdir()
        (plugin_repo / ".plugin" / "plugin.json").write_text(
            '{"name": "test-plugin", "version": "1.0.0", "description": "Test"}'
        )
        subprocess.run(["git", "add", "."], cwd=plugin_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=plugin_repo, check=True)

        # Fetch it
        cache_dir = tmp_path / "cache"
        result = fetch_plugin(f"file://{plugin_repo}", cache_dir=cache_dir)

        assert result.exists()
        assert (result / ".plugin" / "plugin.json").exists()

    def test_fetch_caches_and_updates(self, tmp_path: Path):
        """Test that fetch caches and updates work correctly."""
        # Create plugin repo
        plugin_repo = tmp_path / "plugin"
        plugin_repo.mkdir()
        subprocess.run(["git", "init"], cwd=plugin_repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=plugin_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=plugin_repo, check=True
        )
        (plugin_repo / "version.txt").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=plugin_repo, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=plugin_repo, check=True)

        cache_dir = tmp_path / "cache"

        # First fetch
        result1 = fetch_plugin(f"file://{plugin_repo}", cache_dir=cache_dir)
        assert (result1 / "version.txt").read_text() == "v1"

        # Update source
        (plugin_repo / "version.txt").write_text("v2")
        subprocess.run(["git", "add", "."], cwd=plugin_repo, check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=plugin_repo, check=True)

        # Fetch with update=True
        result2 = fetch_plugin(
            f"file://{plugin_repo}", cache_dir=cache_dir, update=True
        )
        assert result1 == result2  # Same cache path
        assert (result2 / "version.txt").read_text() == "v2"

    def test_fetch_with_ref(self, tmp_path: Path):
        """Test fetching a specific ref."""
        # Create plugin repo with tags
        plugin_repo = tmp_path / "plugin"
        plugin_repo.mkdir()
        subprocess.run(["git", "init"], cwd=plugin_repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=plugin_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=plugin_repo, check=True
        )

        # v1
        (plugin_repo / "version.txt").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=plugin_repo, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=plugin_repo, check=True)
        subprocess.run(["git", "tag", "v1.0.0"], cwd=plugin_repo, check=True)

        # v2
        (plugin_repo / "version.txt").write_text("v2")
        subprocess.run(["git", "add", "."], cwd=plugin_repo, check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=plugin_repo, check=True)

        cache_dir = tmp_path / "cache"

        # Fetch v1.0.0
        result = fetch_plugin(
            f"file://{plugin_repo}", cache_dir=cache_dir, ref="v1.0.0"
        )
        assert (result / "version.txt").read_text() == "v1"


class TestPluginFetchMethodIntegration:
    """Integration tests for Plugin.fetch() classmethod."""

    def test_fetch_and_load_plugin(self, tmp_path: Path):
        """Test the full fetch and load workflow."""
        # Create a complete plugin
        plugin_repo = tmp_path / "complete-plugin"
        plugin_repo.mkdir()
        subprocess.run(["git", "init"], cwd=plugin_repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=plugin_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=plugin_repo, check=True
        )

        # Create plugin structure
        (plugin_repo / ".plugin").mkdir()
        (plugin_repo / ".plugin" / "plugin.json").write_text(
            """{
            "name": "complete-plugin",
            "version": "1.0.0",
            "description": "A complete test plugin"
        }"""
        )

        subprocess.run(["git", "add", "."], cwd=plugin_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=plugin_repo, check=True)

        # Fetch and load
        cache_dir = tmp_path / "cache"
        plugin_path = Plugin.fetch(f"file://{plugin_repo}", cache_dir=cache_dir)
        plugin = Plugin.load(plugin_path)

        assert plugin.name == "complete-plugin"
        assert plugin.version == "1.0.0"
        assert plugin.description == "A complete test plugin"
