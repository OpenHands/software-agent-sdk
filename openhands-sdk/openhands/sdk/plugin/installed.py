"""Installed plugins management for OpenHands SDK.

Public API for managing plugins installed in the user's home directory.
All heavy lifting is delegated to ``InstallationManager``.
"""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
    InstallationManager,
)
from openhands.sdk.plugin.plugin import Plugin


# Public type alias — keeps existing import sites working.
InstalledPluginInfo = InstallationInfo

DEFAULT_INSTALLED_PLUGINS_DIR = Path.home() / ".openhands" / "plugins" / "installed"


def get_installed_plugins_dir() -> Path:
    """Get the default directory for installed plugins."""
    return DEFAULT_INSTALLED_PLUGINS_DIR


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class PluginInstallationInterface(InstallationInterface[Plugin]):
    @staticmethod
    def load_from_dir(extension_dir: Path) -> Plugin:
        return Plugin.load(extension_dir)


def _resolve_installed_dir(installed_dir: Path | None) -> Path:
    return installed_dir if installed_dir is not None else DEFAULT_INSTALLED_PLUGINS_DIR


def _manager(installed_dir: Path) -> InstallationManager[Plugin]:
    return InstallationManager(
        installation_dir=installed_dir,
        installation_interface=PluginInstallationInterface(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_plugin(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    installed_dir: Path | None = None,
    force: bool = False,
) -> InstalledPluginInfo:
    """Install a plugin from a source.

    Args:
        source: Plugin source — ``"github:owner/repo"``, git URL, or
            local path.
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory path within the repository (for monorepos).
        installed_dir: Directory for installed plugins.
            Defaults to ``~/.openhands/plugins/installed/``.
        force: If True, overwrite existing installation.

    Returns:
        InstalledPluginInfo with details about the installation.
    """
    return _manager(_resolve_installed_dir(installed_dir)).install(
        source, ref=ref, repo_path=repo_path, force=force
    )


def uninstall_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Uninstall a plugin by name.

    Returns:
        True if the plugin was uninstalled, False if it wasn't installed.
    """
    return _manager(_resolve_installed_dir(installed_dir)).uninstall(name)


def enable_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Enable an installed plugin by name."""
    return _manager(_resolve_installed_dir(installed_dir)).enable(name)


def disable_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Disable an installed plugin by name."""
    return _manager(_resolve_installed_dir(installed_dir)).disable(name)


def list_installed_plugins(
    installed_dir: Path | None = None,
) -> list[InstalledPluginInfo]:
    """List all installed plugins.

    Self-healing: reconciles metadata with what is on disk.
    """
    return _manager(_resolve_installed_dir(installed_dir)).list_installed()


def load_installed_plugins(
    installed_dir: Path | None = None,
) -> list[Plugin]:
    """Load all enabled installed plugins as ``Plugin`` objects."""
    return _manager(_resolve_installed_dir(installed_dir)).load_installed()


def get_installed_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo | None:
    """Get information about a specific installed plugin."""
    return _manager(_resolve_installed_dir(installed_dir)).get(name)


def update_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo | None:
    """Update an installed plugin to the latest version."""
    return _manager(_resolve_installed_dir(installed_dir)).update(name)
