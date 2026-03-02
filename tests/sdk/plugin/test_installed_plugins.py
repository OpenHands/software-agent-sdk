"""Tests for installed plugins management."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openhands.sdk.plugin import (
    InstalledPluginInfo,
    InstalledPluginsMetadata,
    Plugin,
    get_installed_plugin,
    get_installed_plugins_dir,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)
from openhands.sdk.plugin.installed import (
    INSTALLED_METADATA_FILE,
    _load_metadata,
)


@pytest.fixture
def installed_dir(tmp_path: Path) -> Path:
    """Create a temporary installed plugins directory."""
    installed = tmp_path / "installed"
    installed.mkdir(parents=True)
    return installed


@pytest.fixture
def sample_plugin_dir(tmp_path: Path) -> Path:
    """Create a sample plugin directory structure."""
    plugin_dir = tmp_path / "sample-plugin"
    plugin_dir.mkdir(parents=True)

    # Create plugin manifest
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir()
    manifest = {
        "name": "sample-plugin",
        "version": "1.0.0",
        "description": "A sample plugin for testing",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))

    # Create a skill
    skills_dir = plugin_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    skill_content = """---
name: test-skill
description: A test skill
triggers:
  - test
---
# Test Skill

This is a test skill.
"""
    (skills_dir / "SKILL.md").write_text(skill_content)

    return plugin_dir


class TestInstalledPluginInfo:
    """Tests for InstalledPluginInfo model."""

    def test_from_plugin(self, sample_plugin_dir: Path, tmp_path: Path):
        """Test creating InstalledPluginInfo from a Plugin."""
        plugin = Plugin.load(sample_plugin_dir)
        install_path = tmp_path / "installed" / "sample-plugin"

        info = InstalledPluginInfo.from_plugin(
            plugin=plugin,
            source="github:owner/sample-plugin",
            resolved_ref="abc123",
            repo_path=None,
            install_path=install_path,
        )

        assert info.name == "sample-plugin"
        assert info.version == "1.0.0"
        assert info.description == "A sample plugin for testing"
        assert info.source == "github:owner/sample-plugin"
        assert info.resolved_ref == "abc123"
        assert info.repo_path is None
        assert info.installed_at is not None
        assert str(install_path) in info.install_path


class TestInstalledPluginsMetadata:
    """Tests for InstalledPluginsMetadata model."""

    def test_load_nonexistent(self, tmp_path: Path):
        """Test loading metadata from nonexistent file returns empty."""
        metadata_path = tmp_path / "nonexistent.json"
        metadata = InstalledPluginsMetadata.load(metadata_path)
        assert metadata.plugins == {}

    def test_load_and_save(self, tmp_path: Path):
        """Test saving and loading metadata."""
        metadata_path = tmp_path / INSTALLED_METADATA_FILE

        # Create metadata with a plugin
        info = InstalledPluginInfo(
            name="test-plugin",
            version="1.0.0",
            description="Test",
            source="github:owner/test",
            installed_at="2024-01-01T00:00:00Z",
            install_path="/path/to/plugin",
        )
        metadata = InstalledPluginsMetadata(plugins={"test-plugin": info})
        metadata.save(metadata_path)

        # Load and verify
        loaded = InstalledPluginsMetadata.load(metadata_path)
        assert "test-plugin" in loaded.plugins
        assert loaded.plugins["test-plugin"].name == "test-plugin"
        assert loaded.plugins["test-plugin"].version == "1.0.0"

    def test_load_invalid_json(self, tmp_path: Path):
        """Test loading invalid JSON returns empty metadata."""
        metadata_path = tmp_path / INSTALLED_METADATA_FILE
        metadata_path.write_text("invalid json {")

        metadata = InstalledPluginsMetadata.load(metadata_path)
        assert metadata.plugins == {}


class TestGetInstalledPluginsDir:
    """Tests for get_installed_plugins_dir function."""

    def test_returns_default_path(self):
        """Test that default path is under ~/.openhands/plugins/installed/."""
        path = get_installed_plugins_dir()
        assert ".openhands" in str(path)
        assert "plugins" in str(path)
        assert "installed" in str(path)


class TestInstallPlugin:
    """Tests for install_plugin function."""

    def test_install_from_local_path(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test installing a plugin from a local path."""
        info = install_plugin(
            source=str(sample_plugin_dir),
            installed_dir=installed_dir,
        )

        assert info.name == "sample-plugin"
        assert info.version == "1.0.0"
        assert info.source == str(sample_plugin_dir)

        # Verify plugin was copied
        plugin_path = installed_dir / "sample-plugin"
        assert plugin_path.exists()
        assert (plugin_path / ".plugin" / "plugin.json").exists()

        # Verify metadata was updated
        metadata = _load_metadata(installed_dir)
        assert "sample-plugin" in metadata.plugins

    def test_install_already_exists_raises_error(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test that installing an existing plugin raises FileExistsError."""
        # Install first time
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Try to install again
        with pytest.raises(FileExistsError, match="already installed"):
            install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    def test_install_with_force_overwrites(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test that force=True overwrites existing installation."""
        # Install first time
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Modify the installed plugin
        marker_file = installed_dir / "sample-plugin" / "marker.txt"
        marker_file.write_text("original")

        # Install again with force
        install_plugin(
            source=str(sample_plugin_dir),
            installed_dir=installed_dir,
            force=True,
        )

        # Verify marker file is gone (plugin was replaced)
        assert not marker_file.exists()

    @patch("openhands.sdk.plugin.installed.fetch_plugin_with_resolution")
    def test_install_from_github(
        self, mock_fetch, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test installing a plugin from GitHub."""
        mock_fetch.return_value = (sample_plugin_dir, "abc123def456")

        info = install_plugin(
            source="github:owner/sample-plugin",
            ref="v1.0.0",
            installed_dir=installed_dir,
        )

        mock_fetch.assert_called_once_with(
            source="github:owner/sample-plugin",
            ref="v1.0.0",
            repo_path=None,
            update=True,
        )
        assert info.name == "sample-plugin"
        assert info.source == "github:owner/sample-plugin"
        assert info.resolved_ref == "abc123def456"

    def test_install_invalid_plugin_name_raises_error(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test that installing a plugin with an invalid manifest name fails."""
        manifest_path = sample_plugin_dir / ".plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["name"] = "bad_name"  # not kebab-case
        manifest_path.write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="Invalid plugin name"):
            install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)


class TestUninstallPlugin:
    """Tests for uninstall_plugin function."""

    def test_uninstall_existing_plugin(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test uninstalling an existing plugin."""
        # Install first
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Uninstall
        result = uninstall_plugin("sample-plugin", installed_dir=installed_dir)

        assert result is True
        assert not (installed_dir / "sample-plugin").exists()

        # Verify metadata was updated
        metadata = _load_metadata(installed_dir)
        assert "sample-plugin" not in metadata.plugins

    def test_uninstall_nonexistent_plugin(self, installed_dir: Path):
        """Test uninstalling a plugin that doesn't exist."""
        result = uninstall_plugin("nonexistent", installed_dir=installed_dir)
        assert result is False

    def test_uninstall_untracked_plugin_does_not_delete(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test that uninstall refuses to delete untracked plugin directories."""
        import shutil

        dest = installed_dir / "untracked-plugin"
        shutil.copytree(sample_plugin_dir, dest)

        manifest_path = dest / ".plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["name"] = "untracked-plugin"
        manifest_path.write_text(json.dumps(manifest))

        result = uninstall_plugin("untracked-plugin", installed_dir=installed_dir)

        assert result is False
        assert dest.exists()

    def test_uninstall_invalid_name_raises_error(self, installed_dir: Path):
        """Test that invalid plugin names are rejected."""
        with pytest.raises(ValueError, match="Invalid plugin name"):
            uninstall_plugin("../evil", installed_dir=installed_dir)


class TestListInstalledPlugins:
    """Tests for list_installed_plugins function."""

    def test_list_empty_directory(self, installed_dir: Path):
        """Test listing plugins from empty directory."""
        plugins = list_installed_plugins(installed_dir=installed_dir)
        assert plugins == []

    def test_list_installed_plugins(self, sample_plugin_dir: Path, installed_dir: Path):
        """Test listing installed plugins."""
        # Install a plugin
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # List plugins
        plugins = list_installed_plugins(installed_dir=installed_dir)

        assert len(plugins) == 1
        assert plugins[0].name == "sample-plugin"
        assert plugins[0].version == "1.0.0"

    def test_list_discovers_untracked_plugins(
        self, sample_plugin_dir: Path, installed_dir: Path, tmp_path: Path
    ):
        """Test that list discovers plugins not in metadata."""
        # Manually copy a plugin without using install_plugin
        import shutil

        dest = installed_dir / "manual-plugin"
        shutil.copytree(sample_plugin_dir, dest)

        # Update the manifest to have a different name
        manifest_path = dest / ".plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["name"] = "manual-plugin"
        manifest_path.write_text(json.dumps(manifest))

        # List should discover it
        plugins = list_installed_plugins(installed_dir=installed_dir)

        assert len(plugins) == 1
        assert plugins[0].name == "manual-plugin"
        assert plugins[0].source == "local"  # Unknown source

    def test_list_cleans_up_missing_plugins(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test that list removes metadata for missing plugins."""
        # Install a plugin
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Manually remove the plugin directory
        import shutil

        shutil.rmtree(installed_dir / "sample-plugin")

        # List should clean up metadata
        plugins = list_installed_plugins(installed_dir=installed_dir)

        assert len(plugins) == 0

        # Verify metadata was cleaned
        metadata = _load_metadata(installed_dir)
        assert "sample-plugin" not in metadata.plugins


class TestLoadInstalledPlugins:
    """Tests for load_installed_plugins function."""

    def test_load_empty_directory(self, installed_dir: Path):
        """Test loading plugins from empty directory."""
        plugins = load_installed_plugins(installed_dir=installed_dir)
        assert plugins == []

    def test_load_installed_plugins(self, sample_plugin_dir: Path, installed_dir: Path):
        """Test loading installed plugins."""
        # Install a plugin
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Load plugins
        plugins = load_installed_plugins(installed_dir=installed_dir)

        assert len(plugins) == 1
        assert plugins[0].name == "sample-plugin"
        assert len(plugins[0].skills) == 1


class TestGetInstalledPlugin:
    """Tests for get_installed_plugin function."""

    def test_get_existing_plugin(self, sample_plugin_dir: Path, installed_dir: Path):
        """Test getting info for an existing plugin."""
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        info = get_installed_plugin("sample-plugin", installed_dir=installed_dir)

        assert info is not None
        assert info.name == "sample-plugin"

    def test_get_nonexistent_plugin(self, installed_dir: Path):
        """Test getting info for a nonexistent plugin."""
        info = get_installed_plugin("nonexistent", installed_dir=installed_dir)
        assert info is None

    def test_get_plugin_with_missing_directory(
        self, sample_plugin_dir: Path, installed_dir: Path
    ):
        """Test getting info when plugin directory is missing."""
        # Install a plugin
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Manually remove the directory
        import shutil

        shutil.rmtree(installed_dir / "sample-plugin")

        # Should return None since directory is missing
        info = get_installed_plugin("sample-plugin", installed_dir=installed_dir)
        assert info is None


class TestUpdatePlugin:
    """Tests for update_plugin function."""

    def test_update_existing_plugin(self, sample_plugin_dir: Path, installed_dir: Path):
        """Test updating an existing plugin."""
        # Install first (without mocking)
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

        # Now mock fetch for the update call
        with patch(
            "openhands.sdk.plugin.installed.fetch_plugin_with_resolution"
        ) as mock_fetch:
            mock_fetch.return_value = (sample_plugin_dir, "newcommit123")

            # Update
            info = update_plugin("sample-plugin", installed_dir=installed_dir)

            assert info is not None
            assert info.resolved_ref == "newcommit123"

            # Verify fetch was called with original source but no ref (get latest)
            mock_fetch.assert_called_once()
            call_kwargs = mock_fetch.call_args[1]
            assert call_kwargs["source"] == str(sample_plugin_dir)
            assert call_kwargs["ref"] is None  # Get latest

    def test_update_nonexistent_plugin(self, installed_dir: Path):
        """Test updating a plugin that doesn't exist."""
        info = update_plugin("nonexistent", installed_dir=installed_dir)
        assert info is None
