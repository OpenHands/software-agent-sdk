"""Plugins service for OpenHands Agent Server.

Business logic for installed-plugin management — thin wrappers over the SDK's
installed-plugins subsystem (``openhands.sdk.plugin``) — plus listing the
locally-available plugins. Mirrors ``skills_service.py``; the router stays
focused on HTTP concerns.
"""

from pathlib import Path

from openhands.sdk.plugin import (
    InstalledPluginInfo,
    Plugin,
    disable_plugin,
    enable_plugin,
    get_installed_plugin,
    install_plugin,
    list_installed_plugins,
    uninstall_plugin,
    update_plugin,
)


def service_install_plugin(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    force: bool = False,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo:
    """Install a plugin from a source into the installed-plugins directory."""
    return install_plugin(
        source=source,
        ref=ref,
        repo_path=repo_path,
        force=force,
        installed_dir=installed_dir,
    )


def service_uninstall_plugin(name: str, installed_dir: Path | None = None) -> bool:
    """Uninstall a plugin by name. Returns False if it wasn't installed."""
    return uninstall_plugin(name, installed_dir=installed_dir)


def service_enable_plugin(name: str, installed_dir: Path | None = None) -> bool:
    """Enable an installed plugin. Returns False if it isn't installed."""
    return enable_plugin(name, installed_dir=installed_dir)


def service_disable_plugin(name: str, installed_dir: Path | None = None) -> bool:
    """Disable an installed plugin. Returns False if it isn't installed."""
    return disable_plugin(name, installed_dir=installed_dir)


def service_list_installed_plugins(
    installed_dir: Path | None = None,
) -> list[InstalledPluginInfo]:
    """List all installed plugins (enabled and disabled)."""
    return list_installed_plugins(installed_dir=installed_dir)


def service_get_installed_plugin(
    name: str, installed_dir: Path | None = None
) -> InstalledPluginInfo | None:
    """Get a specific installed plugin, or None if it isn't installed."""
    return get_installed_plugin(name, installed_dir=installed_dir)


def service_update_plugin(
    name: str, installed_dir: Path | None = None
) -> InstalledPluginInfo | None:
    """Update an installed plugin, or None if it isn't installed."""
    return update_plugin(name, installed_dir=installed_dir)


def service_list_available_plugins(
    load_user: bool = True,
    load_project: bool = True,
    project_dir: str | None = None,
) -> list[Plugin]:
    """List locally-available plugins (enabled installed + user/project dirs).

    ``load_available_plugins`` is provided by the "Wire installed + local plugin
    auto-load" ticket (``openhands.sdk.plugin.discovery``). It is imported lazily
    so this module imports cleanly before that ticket is merged; this endpoint
    becomes functional once it lands.
    """
    from openhands.sdk.plugin import load_available_plugins  # type: ignore

    available = load_available_plugins(
        work_dir=project_dir,
        include_user=load_user,
        include_project=load_project,
    )
    return list(available.values())
