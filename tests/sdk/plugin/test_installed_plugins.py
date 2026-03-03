"""Tests for installed plugins management.

This module contains both unit tests and integration tests for plugin
installation, management, and lifecycle operations.

Unit tests use mocks for external operations (GitHub fetch).
Integration tests (marked with @pytest.mark.network) test real GitHub cloning.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from openhands.sdk.plugin import (
    InstalledPluginInfo,
    InstalledPluginsMetadata,
    Plugin,
    PluginFetchError,
    get_installed_plugin,
    get_installed_plugins_dir,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)


# ============================================================================
# Fixtures
# ============================================================================


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


# ============================================================================
# Model Tests
# ============================================================================


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

    def test_load_from_dir_nonexistent(self, tmp_path: Path):
        """Test loading metadata from nonexistent directory returns empty."""
        metadata = InstalledPluginsMetadata.load_from_dir(tmp_path / "nonexistent")
        assert metadata.plugins == {}

    def test_load_from_dir_and_save_to_dir(self, tmp_path: Path):
        """Test saving and loading metadata."""
        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        info = InstalledPluginInfo(
            name="test-plugin",
            version="1.0.0",
            description="Test",
            source="github:owner/test",
            installed_at="2024-01-01T00:00:00Z",
            install_path="/path/to/plugin",
        )
        metadata = InstalledPluginsMetadata(plugins={"test-plugin": info})
        metadata.save_to_dir(installed_dir)

        loaded = InstalledPluginsMetadata.load_from_dir(installed_dir)
        assert "test-plugin" in loaded.plugins
        assert loaded.plugins["test-plugin"].name == "test-plugin"
        assert loaded.plugins["test-plugin"].version == "1.0.0"

    def test_load_from_dir_invalid_json(self, tmp_path: Path):
        """Test loading invalid JSON returns empty metadata."""
        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()
        metadata_path = InstalledPluginsMetadata.get_path(installed_dir)
        metadata_path.write_text("invalid json {")

        metadata = InstalledPluginsMetadata.load_from_dir(installed_dir)
        assert metadata.plugins == {}


# ============================================================================
# Utility Function Tests
# ============================================================================


def test_get_installed_plugins_dir_returns_default_path():
    """Test that default path is under ~/.openhands/plugins/installed/."""
    path = get_installed_plugins_dir()
    assert ".openhands" in str(path)
    assert "plugins" in str(path)
    assert "installed" in str(path)


# ============================================================================
# Install Plugin Tests
# ============================================================================


def test_install_from_local_path(sample_plugin_dir: Path, installed_dir: Path) -> None:
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
    metadata = InstalledPluginsMetadata.load_from_dir(installed_dir)
    assert "sample-plugin" in metadata.plugins


def test_install_already_exists_raises_error(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test that installing an existing plugin raises FileExistsError."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    with pytest.raises(FileExistsError, match="already installed"):
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)


def test_install_with_force_overwrites(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test that force=True overwrites existing installation."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    marker_file = installed_dir / "sample-plugin" / "marker.txt"
    marker_file.write_text("original")

    install_plugin(
        source=str(sample_plugin_dir),
        installed_dir=installed_dir,
        force=True,
    )

    assert not marker_file.exists()


@patch("openhands.sdk.plugin.installed.fetch_plugin_with_resolution")
def test_install_from_github_mocked(
    mock_fetch, sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test installing a plugin from GitHub (mocked)."""
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
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test that installing a plugin with an invalid manifest name fails."""
    manifest_path = sample_plugin_dir / ".plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["name"] = "bad_name"  # not kebab-case
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="Invalid plugin name"):
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)


# ============================================================================
# Uninstall Plugin Tests
# ============================================================================


def test_uninstall_existing_plugin(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test uninstalling an existing plugin."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    result = uninstall_plugin("sample-plugin", installed_dir=installed_dir)

    assert result is True
    assert not (installed_dir / "sample-plugin").exists()

    metadata = InstalledPluginsMetadata.load_from_dir(installed_dir)
    assert "sample-plugin" not in metadata.plugins


def test_uninstall_nonexistent_plugin(installed_dir: Path) -> None:
    """Test uninstalling a plugin that doesn't exist."""
    result = uninstall_plugin("nonexistent", installed_dir=installed_dir)
    assert result is False


def test_uninstall_untracked_plugin_does_not_delete(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test that uninstall refuses to delete untracked plugin directories."""
    dest = installed_dir / "untracked-plugin"
    shutil.copytree(sample_plugin_dir, dest)

    manifest_path = dest / ".plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["name"] = "untracked-plugin"
    manifest_path.write_text(json.dumps(manifest))

    result = uninstall_plugin("untracked-plugin", installed_dir=installed_dir)

    assert result is False
    assert dest.exists()


def test_uninstall_invalid_name_raises_error(installed_dir: Path) -> None:
    """Test that invalid plugin names are rejected."""
    with pytest.raises(ValueError, match="Invalid plugin name"):
        uninstall_plugin("../evil", installed_dir=installed_dir)


# ============================================================================
# List Installed Plugins Tests
# ============================================================================


def test_list_empty_directory(installed_dir: Path) -> None:
    """Test listing plugins from empty directory."""
    plugins = list_installed_plugins(installed_dir=installed_dir)
    assert plugins == []


def test_list_installed_plugins(sample_plugin_dir: Path, installed_dir: Path) -> None:
    """Test listing installed plugins."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    plugins = list_installed_plugins(installed_dir=installed_dir)

    assert len(plugins) == 1
    assert plugins[0].name == "sample-plugin"
    assert plugins[0].version == "1.0.0"


def test_list_discovers_untracked_plugins(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test that list discovers plugins not in metadata."""
    dest = installed_dir / "manual-plugin"
    shutil.copytree(sample_plugin_dir, dest)

    manifest_path = dest / ".plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["name"] = "manual-plugin"
    manifest_path.write_text(json.dumps(manifest))

    plugins = list_installed_plugins(installed_dir=installed_dir)

    assert len(plugins) == 1
    assert plugins[0].name == "manual-plugin"
    assert plugins[0].source == "local"


def test_list_cleans_up_missing_plugins(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test that list removes metadata for missing plugins."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    shutil.rmtree(installed_dir / "sample-plugin")

    plugins = list_installed_plugins(installed_dir=installed_dir)

    assert len(plugins) == 0
    metadata = InstalledPluginsMetadata.load_from_dir(installed_dir)
    assert "sample-plugin" not in metadata.plugins


# ============================================================================
# Load Installed Plugins Tests
# ============================================================================


def test_load_empty_directory(installed_dir: Path) -> None:
    """Test loading plugins from empty directory."""
    plugins = load_installed_plugins(installed_dir=installed_dir)
    assert plugins == []


def test_load_installed_plugins(sample_plugin_dir: Path, installed_dir: Path) -> None:
    """Test loading installed plugins."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    plugins = load_installed_plugins(installed_dir=installed_dir)

    assert len(plugins) == 1
    assert plugins[0].name == "sample-plugin"
    assert len(plugins[0].skills) == 1


# ============================================================================
# Get Installed Plugin Tests
# ============================================================================


def test_get_existing_plugin(sample_plugin_dir: Path, installed_dir: Path) -> None:
    """Test getting info for an existing plugin."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    info = get_installed_plugin("sample-plugin", installed_dir=installed_dir)

    assert info is not None
    assert info.name == "sample-plugin"


def test_get_nonexistent_plugin(installed_dir: Path) -> None:
    """Test getting info for a nonexistent plugin."""
    info = get_installed_plugin("nonexistent", installed_dir=installed_dir)
    assert info is None


def test_get_plugin_with_missing_directory(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test getting info when plugin directory is missing."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    shutil.rmtree(installed_dir / "sample-plugin")

    info = get_installed_plugin("sample-plugin", installed_dir=installed_dir)
    assert info is None


# ============================================================================
# Update Plugin Tests
# ============================================================================


def test_update_existing_plugin_local(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test updating an installed plugin from local source."""
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    # Modify the source to new version
    (sample_plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sample-plugin",
                "version": "1.0.1",
                "description": "Updated plugin",
            }
        )
    )

    updated = update_plugin("sample-plugin", installed_dir=installed_dir)

    assert updated is not None
    assert updated.version == "1.0.1"


def test_update_existing_plugin_mocked(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    """Test updating fetches with ref=None to get latest."""
    # Install first without mocking
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    # Now mock for the update call only
    with patch(
        "openhands.sdk.plugin.installed.fetch_plugin_with_resolution"
    ) as mock_fetch:
        mock_fetch.return_value = (sample_plugin_dir, "newcommit123")

        info = update_plugin("sample-plugin", installed_dir=installed_dir)

        assert info is not None
        assert info.resolved_ref == "newcommit123"

        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args[1]
        assert call_kwargs["source"] == str(sample_plugin_dir)
        assert call_kwargs["ref"] is None  # Get latest


def test_update_nonexistent_plugin(installed_dir: Path) -> None:
    """Test updating a plugin that doesn't exist."""
    info = update_plugin("nonexistent", installed_dir=installed_dir)
    assert info is None


# ============================================================================
# Root-Level SKILL.md Tests (flat format support)
# ============================================================================


def test_load_plugin_with_root_skill_md(tmp_path: Path) -> None:
    """Test loading a plugin with SKILL.md at the root (flat format).

    This tests support for the anthropics/skills format where SKILL.md
    is at the plugin root instead of in a skills/ subdirectory.
    """
    plugin_dir = tmp_path / "flat-plugin"
    plugin_dir.mkdir(parents=True)

    # Create plugin manifest
    (plugin_dir / ".plugin").mkdir()
    (plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "flat-plugin",
                "version": "1.0.0",
                "description": "A flat-format plugin",
            }
        )
    )

    # Create SKILL.md at root (not in skills/ subdirectory)
    (plugin_dir / "SKILL.md").write_text(
        """---
name: flat-skill
description: A skill at the root level
---
# Flat Skill

This skill is at the plugin root, not in skills/ subdirectory.
"""
    )

    # Load the plugin
    plugin = Plugin.load(plugin_dir)

    assert plugin.name == "flat-plugin"
    skills = plugin.get_all_skills()
    assert len(skills) == 1
    assert skills[0].name == "flat-skill"


def test_load_plugin_prefers_skills_dir_over_root(tmp_path: Path) -> None:
    """Test that skills/ directory takes precedence over root SKILL.md."""
    plugin_dir = tmp_path / "mixed-plugin"
    plugin_dir.mkdir(parents=True)

    # Create plugin manifest
    (plugin_dir / ".plugin").mkdir()
    (plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "mixed-plugin",
                "version": "1.0.0",
                "description": "Plugin with both formats",
            }
        )
    )

    # Create SKILL.md at root
    (plugin_dir / "SKILL.md").write_text(
        """---
name: root-skill
description: Skill at root
---
Root skill content.
"""
    )

    # Create skill in skills/ subdirectory
    skills_dir = plugin_dir / "skills" / "nested-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        """---
name: nested-skill
description: Skill in skills/ directory
---
Nested skill content.
"""
    )

    # Load the plugin - should prefer skills/ directory
    plugin = Plugin.load(plugin_dir)

    skills = plugin.get_all_skills()
    assert len(skills) == 1
    assert skills[0].name == "nested-skill"  # Not root-skill


# ============================================================================
# Integration Tests (Real GitHub)
# ============================================================================


@pytest.mark.network
def test_install_from_github_with_repo_path(installed_dir: Path) -> None:
    """Test installing a plugin from GitHub using repo_path for monorepo."""
    try:
        info = install_plugin(
            source="github:OpenHands/agent-sdk",
            repo_path=(
                "examples/05_skills_and_plugins/"
                "02_loading_plugins/example_plugins/code-quality"
            ),
            installed_dir=installed_dir,
        )

        assert info.name == "code-quality"
        assert info.source == "github:OpenHands/agent-sdk"
        assert info.resolved_ref is not None
        assert info.repo_path is not None

        plugins = load_installed_plugins(installed_dir=installed_dir)
        code_quality = next((p for p in plugins if p.name == "code-quality"), None)
        assert code_quality is not None
        assert len(code_quality.get_all_skills()) >= 1

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")


@pytest.mark.network
def test_install_from_github_with_ref(installed_dir: Path) -> None:
    """Test installing a plugin from GitHub with specific ref."""
    try:
        info = install_plugin(
            source="github:OpenHands/agent-sdk",
            ref="main",
            repo_path=(
                "examples/05_skills_and_plugins/"
                "02_loading_plugins/example_plugins/code-quality"
            ),
            installed_dir=installed_dir,
        )

        assert info.name == "code-quality"
        assert info.resolved_ref is not None
        assert len(info.resolved_ref) == 40  # SHA length

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")


@pytest.mark.network
def test_install_from_anthropic_skills(installed_dir: Path) -> None:
    """Test installing a skill from anthropics/skills repository.

    This tests the Claude Code skill format where SKILL.md is at the root.
    The SDK should detect and load the root-level SKILL.md as the plugin's skill.
    """
    try:
        info = install_plugin(
            source="github:anthropics/skills",
            repo_path="skills/pptx",
            ref="main",
            installed_dir=installed_dir,
        )

        assert info.name == "pptx"
        assert info.source == "github:anthropics/skills"
        assert info.repo_path == "skills/pptx"

        install_path = Path(info.install_path)
        skill_md = install_path / "SKILL.md"
        assert skill_md.exists()

        content = skill_md.read_text()
        assert "name: pptx" in content
        assert "description:" in content

        # Verify the skill is loaded (tests root-level SKILL.md support)
        plugins = load_installed_plugins(installed_dir=installed_dir)
        pptx_plugin = next((p for p in plugins if p.name == "pptx"), None)
        assert pptx_plugin is not None
        skills = pptx_plugin.get_all_skills()
        assert len(skills) == 1
        assert skills[0].name == "pptx"

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")
