"""Integration tests for plugin installation scenarios.

These tests verify all the plugin installation scenarios supported by the SDK:
- Local path sources
- GitHub shorthand (github:owner/repo)
- GitHub with ref (branch/tag/commit pinning)
- GitHub with repo_path (monorepo subdirectories)
- Third-party skill repos (e.g., anthropics/skills)
"""

import json
from pathlib import Path

import pytest

from openhands.sdk.plugin import (
    PluginFetchError,
    get_installed_plugin,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)


@pytest.fixture
def installed_dir(tmp_path: Path) -> Path:
    """Create a temporary installed plugins directory."""
    installed = tmp_path / "installed"
    installed.mkdir(parents=True)
    return installed


@pytest.fixture
def local_plugin_source(tmp_path: Path) -> Path:
    """Create a local plugin directory structure."""
    plugin_dir = tmp_path / "local-plugin"
    plugin_dir.mkdir(parents=True)

    # Create plugin manifest
    (plugin_dir / ".plugin").mkdir()
    (plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "local-plugin",
                "version": "1.0.0",
                "description": "Test local plugin",
            }
        )
    )

    # Create a skill
    skill_dir = plugin_dir / "skills" / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: hello
description: Say hello
triggers:
  - hello
---

Reply with a short greeting.
"""
    )

    return plugin_dir


def test_install_from_local_source(
    local_plugin_source: Path, installed_dir: Path
) -> None:
    """Test installing a plugin from a local directory path."""
    info = install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    assert info.name == "local-plugin"
    assert info.version == "1.0.0"
    assert info.source == str(local_plugin_source)
    assert (installed_dir / "local-plugin").exists()


def test_list_installed_plugins(local_plugin_source: Path, installed_dir: Path) -> None:
    """Test listing installed plugins."""
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    plugins = list_installed_plugins(installed_dir=installed_dir)

    assert len(plugins) == 1
    assert plugins[0].name == "local-plugin"
    assert plugins[0].version == "1.0.0"


def test_load_installed_plugins(local_plugin_source: Path, installed_dir: Path) -> None:
    """Test loading installed plugins as Plugin objects."""
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    plugins = load_installed_plugins(installed_dir=installed_dir)

    assert len(plugins) == 1
    assert plugins[0].name == "local-plugin"
    assert len(plugins[0].get_all_skills()) == 1


def test_get_installed_plugin(local_plugin_source: Path, installed_dir: Path) -> None:
    """Test getting info for a specific installed plugin."""
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    info = get_installed_plugin("local-plugin", installed_dir=installed_dir)

    assert info is not None
    assert info.name == "local-plugin"


def test_update_plugin(local_plugin_source: Path, installed_dir: Path) -> None:
    """Test updating an installed plugin."""
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    # Modify the source to new version
    (local_plugin_source / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "local-plugin",
                "version": "1.0.1",
                "description": "Updated test plugin",
            }
        )
    )

    updated = update_plugin("local-plugin", installed_dir=installed_dir)

    assert updated is not None
    assert updated.version == "1.0.1"


def test_uninstall_plugin(local_plugin_source: Path, installed_dir: Path) -> None:
    """Test uninstalling a plugin."""
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)
    assert (installed_dir / "local-plugin").exists()

    result = uninstall_plugin("local-plugin", installed_dir=installed_dir)

    assert result is True
    assert not (installed_dir / "local-plugin").exists()
    assert len(list_installed_plugins(installed_dir=installed_dir)) == 0


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
        assert info.resolved_ref is not None  # Should have commit SHA
        assert info.repo_path is not None

        # Load and verify skills
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
        # resolved_ref should be a commit SHA (40 hex chars)
        assert len(info.resolved_ref) == 40

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")


@pytest.mark.network
def test_install_from_anthropic_skills(installed_dir: Path) -> None:
    """Test installing a skill from anthropics/skills repository.

    This tests the Claude Code skill format where SKILL.md is at the root
    of the plugin directory (not in a skills/ subdirectory).
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

        # Verify the SKILL.md exists at root (Claude Code format)
        install_path = Path(info.install_path)
        skill_md = install_path / "SKILL.md"
        assert skill_md.exists()

        # Verify it has expected content
        content = skill_md.read_text()
        assert "name: pptx" in content
        assert "description:" in content

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")


def test_install_force_overwrite(
    local_plugin_source: Path, installed_dir: Path
) -> None:
    """Test force installing overwrites existing plugin."""
    # Install first time
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    # Create marker file
    marker = installed_dir / "local-plugin" / "marker.txt"
    marker.write_text("original")

    # Install with force
    install_plugin(
        source=str(local_plugin_source), installed_dir=installed_dir, force=True
    )

    # Marker should be gone
    assert not marker.exists()


def test_install_without_force_raises_error(
    local_plugin_source: Path, installed_dir: Path
) -> None:
    """Test installing existing plugin without force raises error."""
    install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)

    with pytest.raises(FileExistsError, match="already installed"):
        install_plugin(source=str(local_plugin_source), installed_dir=installed_dir)
