"""Tests for local + installed plugin discovery.

Covers ``openhands.sdk.plugin.discovery`` — the *ambient* plugin set that
auto-loads into conversations: plugins in the user/project directories plus
enabled installed plugins. Mirrors the user/project skill discovery tests.
"""

import json
from pathlib import Path

import pytest

from openhands.sdk.plugin import (
    disable_plugin,
    discovery,
    install_plugin,
    installed,
    load_available_plugins,
    load_project_plugins,
    load_user_plugins,
)


def _make_plugin(plugin_dir: Path, name: str, skill_name: str, content: str) -> Path:
    """Create a minimal plugin directory containing a single skill."""
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": f"Plugin {name}"})
    )
    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / f"{skill_name}.md").write_text(
        f"---\nname: {skill_name}\n---\n{content}"
    )
    return plugin_dir


@pytest.fixture
def installed_dir(tmp_path: Path) -> Path:
    """An empty installed-plugins store."""
    store = tmp_path / "installed-store"
    store.mkdir()
    return store


def test_load_user_plugins_discovers_directory_plugin(
    tmp_path: Path, installed_dir: Path, monkeypatch
):
    # Arrange
    user_dir = tmp_path / ".agents" / "plugins"
    _make_plugin(user_dir / "weather", "weather", "weather-skill", "Weather content.")
    monkeypatch.setattr(discovery, "USER_PLUGINS_DIRS", [user_dir])
    monkeypatch.setattr(installed, "DEFAULT_INSTALLED_PLUGINS_DIR", installed_dir)

    # Act
    plugins = load_user_plugins()

    # Assert
    assert [p.name for p in plugins] == ["weather"]


def test_load_user_plugins_filters_installed_by_enabled_flag(
    tmp_path: Path, installed_dir: Path, monkeypatch
):
    # Arrange: install two plugins, then disable one.
    enabled_src = _make_plugin(tmp_path / "src-on", "enabled-plugin", "s1", "c1")
    disabled_src = _make_plugin(tmp_path / "src-off", "disabled-plugin", "s2", "c2")
    install_plugin(str(enabled_src), installed_dir=installed_dir)
    install_plugin(str(disabled_src), installed_dir=installed_dir)
    disable_plugin("disabled-plugin", installed_dir=installed_dir)
    monkeypatch.setattr(discovery, "USER_PLUGINS_DIRS", [tmp_path / "empty"])
    monkeypatch.setattr(installed, "DEFAULT_INSTALLED_PLUGINS_DIR", installed_dir)

    # Act
    names = {p.name for p in load_user_plugins()}

    # Assert
    assert "enabled-plugin" in names
    assert "disabled-plugin" not in names


def test_load_user_plugins_does_not_load_installed_store_as_plugin(
    tmp_path: Path, monkeypatch
):
    # Arrange: a user plugins dir holding a real plugin AND the install store as
    # a child directory named "installed".
    openhands_plugins = tmp_path / ".openhands" / "plugins"
    _make_plugin(openhands_plugins / "real-plugin", "real-plugin", "rs", "Real.")
    install_store = openhands_plugins / "installed"
    install_store.mkdir(parents=True)
    monkeypatch.setattr(discovery, "USER_PLUGINS_DIRS", [openhands_plugins])
    monkeypatch.setattr(installed, "DEFAULT_INSTALLED_PLUGINS_DIR", install_store)

    # Act
    names = {p.name for p in load_user_plugins()}

    # Assert: the real plugin loads; the install store is not mis-loaded as a
    # bogus plugin named "installed".
    assert "real-plugin" in names
    assert "installed" not in names


def test_load_project_plugins_discovers_workspace_plugin(tmp_path: Path):
    # Arrange
    work_dir = tmp_path / "workspace"
    _make_plugin(
        work_dir / ".agents" / "plugins" / "proj", "proj-plugin", "ps", "Project."
    )

    # Act
    plugins = load_project_plugins(work_dir)

    # Assert
    assert "proj-plugin" in {p.name for p in plugins}


def test_load_available_plugins_project_overrides_user(
    tmp_path: Path, installed_dir: Path, monkeypatch
):
    # Arrange: the same plugin name in a user dir and the project dir, each
    # providing a distinguishable skill.
    user_dir = tmp_path / ".agents" / "plugins"
    _make_plugin(user_dir / "shared", "shared", "user-skill", "User.")
    work_dir = tmp_path / "workspace"
    _make_plugin(
        work_dir / ".agents" / "plugins" / "shared", "shared", "project-skill", "Proj."
    )
    monkeypatch.setattr(discovery, "USER_PLUGINS_DIRS", [user_dir])
    monkeypatch.setattr(installed, "DEFAULT_INSTALLED_PLUGINS_DIR", installed_dir)

    # Act
    available = load_available_plugins(
        work_dir=work_dir, include_user=True, include_project=True
    )

    # Assert: project wins the name conflict.
    assert set(available) == {"shared"}
    skill_names = {s.name for s in available["shared"].skills}
    assert "project-skill" in skill_names
    assert "user-skill" not in skill_names
