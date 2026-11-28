import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from openhands.agent_server.docker_runtime.plugins import refresh_installed_plugins
from openhands.sdk.plugin.installed import (
    disable_plugin,
    enable_plugin,
    install_plugin,
    load_installed_plugins,
    uninstall_plugin,
)


@pytest.fixture
def plugin_factory(tmp_path: Path) -> Callable[[str], Path]:
    def create(name: str) -> Path:
        source = tmp_path / "sources" / name
        (source / ".plugin").mkdir(parents=True)
        (source / ".plugin/plugin.json").write_text(json.dumps({"name": name}))
        (source / "commands").mkdir()
        (source / "commands/ping.md").write_text("---\ndescription: Ping\n---\nPong")
        return source

    return create


@pytest.fixture
def stores(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "host" / "installed"
    home = tmp_path / "runtime" / "persistence"
    home.mkdir(parents=True)
    return source, home, home / "plugins" / "installed"


def test_refresh_preserves_local_plugins_and_reconciles_server_enablement(
    plugin_factory, stores
):
    source, home, target = stores
    install_plugin(str(plugin_factory("host-plugin")), installed_dir=source)
    install_plugin(str(plugin_factory("local-plugin")), installed_dir=target)

    def names() -> set[str]:
        return {plugin.name for plugin in load_installed_plugins(target)}

    refresh_installed_plugins(source, home, "/runtime-home")
    assert names() == {"host-plugin", "local-plugin"}
    disable_plugin("host-plugin", installed_dir=source)
    assert names() == {"host-plugin", "local-plugin"}
    refresh_installed_plugins(source, home, "/runtime-home")
    assert names() == {"local-plugin"}
    enable_plugin("host-plugin", installed_dir=source)
    refresh_installed_plugins(source, home, "/runtime-home")
    assert names() == {"host-plugin", "local-plugin"}
    uninstall_plugin("host-plugin", installed_dir=source)
    refresh_installed_plugins(source, home, "/runtime-home")
    assert names() == {"local-plugin"}


def test_snapshot_needs_neither_original_source_nor_fetch_credentials(
    plugin_factory, stores
):
    source, home, target = stores
    original = plugin_factory("offline-plugin")
    install_plugin(str(original), installed_dir=source)
    shutil.rmtree(original)
    metadata_path = source / ".installed.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["extensions"]["offline-plugin"]["source"] = (
        "https://secret-token@example.invalid/plugin.git"
    )
    metadata_path.write_text(json.dumps(metadata))
    host_metadata_before = metadata_path.read_bytes()
    git_dir = source / "offline-plugin" / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("secret-token")

    refresh_installed_plugins(source, home, "/runtime-home")

    plugin = load_installed_plugins(target)[0]
    assert plugin.name == "offline-plugin"
    assert plugin.get_all_skills()[0].name == "offline-plugin:ping"
    assert not (target / "offline-plugin" / ".git").exists()
    assert "secret-token" not in (target / ".installed.json").read_text()
    assert str(source.parent) not in (target / ".installed.json").read_text()
    assert metadata_path.read_bytes() == host_metadata_before


def test_container_local_installation_wins_on_name_conflict(plugin_factory, stores):
    source, home, target = stores
    original = plugin_factory("shared-plugin")
    install_plugin(str(original), installed_dir=source)
    (original / "commands/ping.md").write_text(
        "---\ndescription: Container-local command\n---\nLocal pong"
    )
    install_plugin(str(original), installed_dir=target)

    refresh_installed_plugins(source, home, "/runtime-home")

    plugin = load_installed_plugins(target)[0]
    assert plugin.get_all_skills()[0].description == "Container-local command"
    disable_plugin("shared-plugin", installed_dir=source)
    refresh_installed_plugins(source, home, "/runtime-home")
    assert load_installed_plugins(target)[0].name == "shared-plugin"


def test_corrupt_host_metadata_keeps_previous_snapshot(plugin_factory, stores, caplog):
    source, home, target = stores
    install_plugin(str(plugin_factory("host-plugin")), installed_dir=source)
    refresh_installed_plugins(source, home, "/runtime-home")
    (source / ".installed.json").write_text("{invalid")

    refresh_installed_plugins(source, home, "/runtime-home")

    assert [plugin.name for plugin in load_installed_plugins(target)] == ["host-plugin"]
    assert "Failed to refresh server-installed plugins" in caplog.text
    assert (source / ".installed.json").read_text() == "{invalid"


def test_one_failed_copy_does_not_hide_other_plugins(
    plugin_factory, stores, monkeypatch, caplog
):
    source, home, target = stores
    for name in ("broken", "working"):
        install_plugin(str(plugin_factory(name)), installed_dir=source)
    copytree = shutil.copytree

    def copy(src, dst, *args, **kwargs):
        if Path(src).name == "broken":
            raise OSError("unreadable plugin")
        return copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", copy)
    refresh_installed_plugins(source, home, "/runtime-home")

    assert [plugin.name for plugin in load_installed_plugins(target)] == ["working"]
    assert "Failed to copy installed plugin 'broken'" in caplog.text


def test_container_symlink_cannot_redirect_snapshot_writes(
    plugin_factory, stores, tmp_path, caplog
):
    source, home, target = stores
    install_plugin(str(plugin_factory("host-plugin")), installed_dir=source)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("unrelated")
    (home / "plugins").symlink_to(outside, target_is_directory=True)

    refresh_installed_plugins(source, home, "/runtime-home")

    assert list(outside.iterdir()) == [outside / "keep"]
    assert (outside / "keep").read_text() == "unrelated"
    assert "Failed to refresh server-installed plugins" in caplog.text


def test_installed_symlink_does_not_copy_unrelated_host_data(plugin_factory, stores):
    source, home, target = stores
    original = plugin_factory("host-plugin")
    install_plugin(str(original), installed_dir=source)
    shutil.rmtree(source / "host-plugin")
    (original / "secret").write_text("unrelated host data")
    (source / "host-plugin").symlink_to(original, target_is_directory=True)

    refresh_installed_plugins(source, home, "/runtime-home")

    assert load_installed_plugins(target) == []
    assert not (target / "host-plugin").exists()
