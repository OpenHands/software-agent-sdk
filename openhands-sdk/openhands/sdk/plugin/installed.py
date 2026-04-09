"""Installed plugins management for OpenHands SDK.

This module provides utilities for managing plugins installed in the user's
home directory (~/.openhands/plugins/installed/).

The installed plugins directory structure follows the Claude Code pattern::

    ~/.openhands/plugins/installed/
    ├── plugin-name-1/
    │   ├── .plugin/
    │   │   └── plugin.json
    │   ├── skills/
    │   └── ...
    ├── plugin-name-2/
    │   └── ...
    └── .installed.json  # Metadata about installed plugins
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from openhands.sdk.extensions import (
    InstalledExtensionInfo,
    InstalledExtensionManager,
    InstalledExtensionMetadata,
)
from openhands.sdk.plugin.plugin import Plugin


# Default directory for installed plugins
DEFAULT_INSTALLED_PLUGINS_DIR = Path.home() / ".openhands" / "plugins" / "installed"

_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def get_installed_plugins_dir() -> Path:
    """Get the default directory for installed plugins.

    Returns:
        Path to ~/.openhands/plugins/installed/
    """
    return DEFAULT_INSTALLED_PLUGINS_DIR


def _validate_plugin_name(name: str) -> None:
    """Validate plugin name is Claude-like kebab-case.

    This protects filesystem operations (install/uninstall) from path traversal.
    """
    if not _PLUGIN_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid plugin name. Expected kebab-case like 'my-plugin' (got {name!r})."
        )


class InstalledPluginInfo(InstalledExtensionInfo):
    """Information about an installed plugin.

    This model tracks metadata about a plugin installation, including
    where it was installed from and when.

    Extends InstalledExtensionInfo with plugin-specific fields.
    """

    version: str = Field(default="1.0.0", description="Plugin version")

    @classmethod
    def from_plugin(
        cls,
        plugin: Plugin,
        source: str,
        resolved_ref: str | None,
        repo_path: str | None,
        install_path: Path,
    ) -> InstalledPluginInfo:
        """Create InstalledPluginInfo from a loaded Plugin."""
        return cls(
            name=plugin.name,
            version=plugin.version,
            description=plugin.description,
            source=source,
            resolved_ref=resolved_ref,
            repo_path=repo_path,
            installed_at=datetime.now(UTC).isoformat(),
            install_path=str(install_path),
        )


# For backward compatibility, provide InstalledPluginsMetadata as a wrapper
# around InstalledExtensionMetadata.
class InstalledPluginsMetadata(InstalledExtensionMetadata[InstalledPluginInfo]):
    """Metadata file for tracking all installed plugins.

    This class wraps InstalledExtensionMetadata for backward compatibility.
    New code should use the InstalledExtensionManager instead.
    """

    def __init__(
        self,
        *,
        items: dict[str, InstalledPluginInfo] | None = None,
        plugins: dict[str, InstalledPluginInfo] | None = None,
    ) -> None:
        """Initialize with either items or plugins keyword argument."""
        # Support both 'items' (new) and 'plugins' (legacy) kwarg
        data = items if items is not None else (plugins or {})
        super().__init__(items=data)

    # Alias 'items' as 'plugins' for backward compatibility
    @property
    def plugins(self) -> dict[str, InstalledPluginInfo]:
        """Get installed plugins (alias for items)."""
        return self.items

    @plugins.setter
    def plugins(self, value: dict[str, InstalledPluginInfo]) -> None:
        """Set installed plugins (alias for items)."""
        self.items = value

    @classmethod
    def load_from_dir(  # type: ignore[override]
        cls, installed_dir: Path
    ) -> InstalledPluginsMetadata:
        """Load metadata from the installed plugins directory."""
        base = InstalledExtensionMetadata.load_from_dir(
            installed_dir, InstalledPluginInfo
        )
        return cls(items=base.items)

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed plugins directory (legacy format)."""
        import json

        metadata_path = self.get_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        # Use "plugins" key for backward compatibility
        data = {
            "plugins": {name: info.model_dump() for name, info in self.items.items()}
        }
        with open(metadata_path, "w") as f:
            json.dump(data, f, indent=2)


def _create_plugin_info(
    plugin: Plugin,
    source: str,
    resolved_ref: str | None,
    repo_path: str | None,
    install_path: Path,
) -> InstalledPluginInfo:
    """Create InstalledPluginInfo from a loaded Plugin (for manager callback)."""
    return InstalledPluginInfo.from_plugin(
        plugin, source, resolved_ref, repo_path, install_path
    )


# Create the plugin manager instance
_plugin_manager: InstalledExtensionManager[Plugin, InstalledPluginInfo] = (
    InstalledExtensionManager(
        default_dir=DEFAULT_INSTALLED_PLUGINS_DIR,
        validate_name=_validate_plugin_name,
        load_item=Plugin.load,
        create_info=_create_plugin_info,
        info_type=InstalledPluginInfo,
    )
)


def install_plugin(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    installed_dir: Path | None = None,
    force: bool = False,
) -> InstalledPluginInfo:
    """Install a plugin from a source.

    Fetches the plugin from the source, copies it to the installed plugins
    directory, and records the installation metadata.

    Args:
        source: Plugin source - can be:
            - "github:owner/repo" - GitHub shorthand
            - Any git URL (GitHub, GitLab, Bitbucket, etc.)
            - Local path (for development/testing)
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory path within the repository (for monorepos).
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/plugins/installed/
        force: If True, overwrite existing installation. If False, raise error
            if plugin is already installed.

    Returns:
        InstalledPluginInfo with details about the installation.

    Raises:
        PluginFetchError: If fetching the plugin fails.
        FileExistsError: If plugin is already installed and force=False.
        ValueError: If the plugin manifest is invalid.

    Example:
        >>> info = install_plugin("github:owner/my-plugin", ref="v1.0.0")
        >>> print(f"Installed {info.name} from {info.source}")
    """
    return _plugin_manager.install(
        source=source,
        ref=ref,
        repo_path=repo_path,
        installed_dir=installed_dir,
        force=force,
    )


def uninstall_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Uninstall a plugin by name.

    Only plugins tracked in the installed plugins metadata file can be uninstalled.
    This avoids deleting arbitrary directories in the installed plugins directory.

    Args:
        name: Name of the plugin to uninstall.
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/plugins/installed/

    Returns:
        True if the plugin was uninstalled, False if it wasn't installed.

    Example:
        >>> if uninstall_plugin("my-plugin"):
        ...     print("Plugin uninstalled")
        ... else:
        ...     print("Plugin was not installed")
    """
    return _plugin_manager.uninstall(name=name, installed_dir=installed_dir)


def enable_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Enable an installed plugin by name."""
    return _plugin_manager.enable(name=name, installed_dir=installed_dir)


def disable_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Disable an installed plugin by name."""
    return _plugin_manager.disable(name=name, installed_dir=installed_dir)


def list_installed_plugins(
    installed_dir: Path | None = None,
) -> list[InstalledPluginInfo]:
    """List all installed plugins.

    This function is self-healing: it may update the installed plugins metadata
    file to remove entries whose directories were deleted, and to add entries for
    plugin directories that were manually copied into the installed dir.

    Args:
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/plugins/installed/

    Returns:
        List of InstalledPluginInfo for each installed plugin.

    Example:
        >>> for info in list_installed_plugins():
        ...     print(f"{info.name} v{info.version} - {info.description}")
    """
    return _plugin_manager.list_installed(installed_dir=installed_dir)


def load_installed_plugins(
    installed_dir: Path | None = None,
) -> list[Plugin]:
    """Load all installed plugins.

    Loads Plugin objects for all plugins in the installed plugins directory.
    This is useful for integrating installed plugins into an agent.

    Args:
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/plugins/installed/

    Returns:
        List of loaded Plugin objects.

    Example:
        >>> plugins = load_installed_plugins()
        >>> for plugin in plugins:
        ...     print(f"Loaded {plugin.name} with {len(plugin.skills)} skills")
    """
    return _plugin_manager.load_installed(installed_dir=installed_dir)


def get_installed_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo | None:
    """Get information about a specific installed plugin.

    Args:
        name: Name of the plugin to look up.
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/plugins/installed/

    Returns:
        InstalledPluginInfo if the plugin is installed, None otherwise.

    Example:
        >>> info = get_installed_plugin("my-plugin")
        >>> if info:
        ...     print(f"Installed from {info.source} at {info.installed_at}")
    """
    return _plugin_manager.get(name=name, installed_dir=installed_dir)


def update_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo | None:
    """Update an installed plugin to the latest version.

    Re-fetches the plugin from its original source and reinstalls it.

    This always updates to the latest version available from the original source
    (i.e., it does not preserve a pinned ref).

    Args:
        name: Name of the plugin to update.
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/plugins/installed/

    Returns:
        Updated InstalledPluginInfo if successful, None if plugin not installed.

    Raises:
        PluginFetchError: If fetching the updated plugin fails.

    Example:
        >>> info = update_plugin("my-plugin")
        >>> if info:
        ...     print(f"Updated to v{info.version}")
    """
    return _plugin_manager.update(name=name, installed_dir=installed_dir)
