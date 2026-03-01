"""Installed plugins management for OpenHands SDK.

This module provides utilities for managing plugins installed in the user's
home directory (~/.openhands/skills/installed/). It supports:

- Installing plugins from GitHub repositories, git URLs, or local paths
- Listing installed plugins with their metadata
- Uninstalling plugins by name
- Loading all installed plugins

The installed plugins directory structure follows the Claude Code pattern:
    ~/.openhands/skills/installed/
    ├── plugin-name-1/
    │   ├── .plugin/
    │   │   └── plugin.json
    │   ├── skills/
    │   └── ...
    ├── plugin-name-2/
    │   └── ...
    └── .installed.json  # Metadata about installed plugins

Example usage:
    >>> from openhands.sdk.plugin.installed import (
    ...     install_plugin,
    ...     list_installed_plugins,
    ...     uninstall_plugin,
    ...     load_installed_plugins,
    ... )
    >>>
    >>> # Install a plugin from GitHub
    >>> info = install_plugin("github:owner/my-plugin")
    >>> print(f"Installed {info.name} v{info.version}")
    >>>
    >>> # List all installed plugins
    >>> for plugin_info in list_installed_plugins():
    ...     print(f"  - {plugin_info.name}: {plugin_info.description}")
    >>>
    >>> # Load plugins for use
    >>> plugins = load_installed_plugins()
    >>>
    >>> # Uninstall a plugin
    >>> uninstall_plugin("my-plugin")
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.fetch import (
    fetch_plugin_with_resolution,
)
from openhands.sdk.plugin.plugin import Plugin


logger = get_logger(__name__)

# Default directory for installed plugins
DEFAULT_INSTALLED_PLUGINS_DIR = Path.home() / ".openhands" / "skills" / "installed"

# Metadata file for tracking installed plugins
INSTALLED_METADATA_FILE = ".installed.json"

PLUGIN_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_plugin_name(name: str) -> None:
    """Validate plugin name is Claude-like kebab-case.

    This protects filesystem operations (install/uninstall) from path traversal.
    """
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid plugin name. Expected kebab-case like 'my-plugin' (got {name!r})."
        )


class InstalledPluginInfo(BaseModel):
    """Information about an installed plugin.

    This model tracks metadata about a plugin installation, including
    where it was installed from and when.
    """

    name: str = Field(description="Plugin name (from manifest)")
    version: str = Field(default="1.0.0", description="Plugin version")
    description: str = Field(default="", description="Plugin description")
    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None,
        description="Resolved git commit SHA (for version pinning)",
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )
    installed_at: str = Field(
        description="ISO 8601 timestamp of installation",
    )
    install_path: str = Field(
        description="Path where the plugin is installed",
    )

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


class InstalledPluginsMetadata(BaseModel):
    """Metadata file for tracking all installed plugins."""

    plugins: dict[str, InstalledPluginInfo] = Field(
        default_factory=dict,
        description="Map of plugin name to installation info",
    )

    @classmethod
    def load(cls, metadata_path: Path) -> InstalledPluginsMetadata:
        """Load metadata from file, or return empty if not found."""
        if not metadata_path.exists():
            return cls()
        try:
            with open(metadata_path) as f:
                data = json.load(f)
            return cls.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load installed plugins metadata: {e}")
            return cls()

    def save(self, metadata_path: Path) -> None:
        """Save metadata to file."""
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with open(metadata_path, "w") as f:
            json.dump(self.model_dump(), f, indent=2)


def get_installed_plugins_dir() -> Path:
    """Get the directory for installed plugins.

    Returns:
        Path to ~/.openhands/skills/installed/
    """
    return DEFAULT_INSTALLED_PLUGINS_DIR


def _get_metadata_path(installed_dir: Path | None = None) -> Path:
    """Get the path to the installed plugins metadata file."""
    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()
    return installed_dir / INSTALLED_METADATA_FILE


def _load_metadata(installed_dir: Path | None = None) -> InstalledPluginsMetadata:
    """Load the installed plugins metadata."""
    return InstalledPluginsMetadata.load(_get_metadata_path(installed_dir))


def _save_metadata(
    metadata: InstalledPluginsMetadata, installed_dir: Path | None = None
) -> None:
    """Save the installed plugins metadata."""
    metadata.save(_get_metadata_path(installed_dir))


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
            Defaults to ~/.openhands/skills/installed/
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
    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()

    # Fetch the plugin (downloads to cache if remote)
    logger.info(f"Fetching plugin from {source}")
    fetched_path, resolved_ref = fetch_plugin_with_resolution(
        source=source,
        ref=ref,
        repo_path=repo_path,
        update=True,
    )

    # Load the plugin to get its metadata
    plugin = Plugin.load(fetched_path)
    plugin_name = plugin.name
    _validate_plugin_name(plugin_name)

    # Check if already installed
    install_path = installed_dir / plugin_name
    if install_path.exists() and not force:
        raise FileExistsError(
            f"Plugin '{plugin_name}' is already installed at {install_path}. "
            f"Use force=True to overwrite."
        )

    # Remove existing installation if force=True
    if install_path.exists():
        logger.info(f"Removing existing installation of '{plugin_name}'")
        shutil.rmtree(install_path)

    # Copy plugin to installed directory
    logger.info(f"Installing plugin '{plugin_name}' to {install_path}")
    installed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fetched_path, install_path)

    # Create installation info
    info = InstalledPluginInfo.from_plugin(
        plugin=plugin,
        source=source,
        resolved_ref=resolved_ref,
        repo_path=repo_path,
        install_path=install_path,
    )

    # Update metadata
    metadata = _load_metadata(installed_dir)
    metadata.plugins[plugin_name] = info
    _save_metadata(metadata, installed_dir)

    logger.info(f"Successfully installed plugin '{plugin_name}' v{plugin.version}")
    return info


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
            Defaults to ~/.openhands/skills/installed/

    Returns:
        True if the plugin was uninstalled, False if it wasn't installed.

    Example:
        >>> if uninstall_plugin("my-plugin"):
        ...     print("Plugin uninstalled")
        ... else:
        ...     print("Plugin was not installed")
    """
    _validate_plugin_name(name)

    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()

    metadata = _load_metadata(installed_dir)
    if name not in metadata.plugins:
        logger.warning(f"Plugin '{name}' is not installed")
        return False

    plugin_path = installed_dir / name
    if plugin_path.exists():
        logger.info(f"Uninstalling plugin '{name}' from {plugin_path}")
        shutil.rmtree(plugin_path)
    else:
        logger.warning(
            f"Plugin '{name}' was tracked but its directory is missing: {plugin_path}"
        )

    del metadata.plugins[name]
    _save_metadata(metadata, installed_dir)

    logger.info(f"Successfully uninstalled plugin '{name}'")
    return True


def list_installed_plugins(
    installed_dir: Path | None = None,
) -> list[InstalledPluginInfo]:
    """List all installed plugins.

    This function is self-healing: it may update the installed plugins metadata
    file to remove entries whose directories were deleted, and to add entries for
    plugin directories that were manually copied into the installed dir.

    Args:
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/skills/installed/

    Returns:
        List of InstalledPluginInfo for each installed plugin.

    Example:
        >>> for info in list_installed_plugins():
        ...     print(f"{info.name} v{info.version} - {info.description}")
    """
    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()

    if not installed_dir.exists():
        return []

    metadata = _load_metadata(installed_dir)
    metadata_changed = False
    installed_plugins: list[InstalledPluginInfo] = []

    # Verify each tracked plugin still exists and collect info.
    for name, info in list(metadata.plugins.items()):
        try:
            _validate_plugin_name(name)
        except ValueError as e:
            logger.warning(f"Invalid tracked plugin name {name!r}, removing: {e}")
            del metadata.plugins[name]
            metadata_changed = True
            continue

        plugin_path = installed_dir / name
        if plugin_path.exists():
            installed_plugins.append(info)
            continue

        logger.warning(f"Plugin '{name}' directory missing, removing from metadata")
        del metadata.plugins[name]
        metadata_changed = True

    # Discover plugins that exist but aren't in metadata (e.g., manual copies).
    for item in installed_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        if item.name in metadata.plugins:
            continue

        try:
            _validate_plugin_name(item.name)
        except ValueError:
            logger.debug(f"Skipping directory with invalid plugin name: {item}")
            continue

        try:
            plugin = Plugin.load(item)
        except Exception as e:
            logger.debug(f"Skipping directory {item}: {e}")
            continue

        if plugin.name != item.name:
            logger.warning(
                "Skipping plugin directory because manifest name doesn't match "
                f"directory name: dir={item.name!r}, manifest={plugin.name!r}"
            )
            continue

        info = InstalledPluginInfo(
            name=plugin.name,
            version=plugin.version,
            description=plugin.description,
            source="local",
            installed_at=datetime.now(UTC).isoformat(),
            install_path=str(item),
        )
        installed_plugins.append(info)
        metadata.plugins[item.name] = info
        metadata_changed = True
        logger.info(f"Discovered untracked plugin: {plugin.name}")

    if metadata_changed:
        _save_metadata(metadata, installed_dir)

    return installed_plugins


def load_installed_plugins(
    installed_dir: Path | None = None,
) -> list[Plugin]:
    """Load all installed plugins.

    Loads Plugin objects for all plugins in the installed plugins directory.
    This is useful for integrating installed plugins into an agent.

    Args:
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/skills/installed/

    Returns:
        List of loaded Plugin objects.

    Example:
        >>> plugins = load_installed_plugins()
        >>> for plugin in plugins:
        ...     print(f"Loaded {plugin.name} with {len(plugin.skills)} skills")
    """
    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()

    if not installed_dir.exists():
        return []

    return Plugin.load_all(installed_dir)


def get_installed_plugin(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo | None:
    """Get information about a specific installed plugin.

    Args:
        name: Name of the plugin to look up.
        installed_dir: Directory for installed plugins.
            Defaults to ~/.openhands/skills/installed/

    Returns:
        InstalledPluginInfo if the plugin is installed, None otherwise.

    Example:
        >>> info = get_installed_plugin("my-plugin")
        >>> if info:
        ...     print(f"Installed from {info.source} at {info.installed_at}")
    """
    _validate_plugin_name(name)

    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()

    metadata = _load_metadata(installed_dir)
    info = metadata.plugins.get(name)

    # Verify the plugin directory still exists
    if info is not None:
        plugin_path = installed_dir / name
        if not plugin_path.exists():
            return None

    return info


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
            Defaults to ~/.openhands/skills/installed/

    Returns:
        Updated InstalledPluginInfo if successful, None if plugin not installed.

    Raises:
        PluginFetchError: If fetching the updated plugin fails.

    Example:
        >>> info = update_plugin("my-plugin")
        >>> if info:
        ...     print(f"Updated to v{info.version}")
    """
    _validate_plugin_name(name)

    if installed_dir is None:
        installed_dir = get_installed_plugins_dir()

    # Get current installation info
    current_info = get_installed_plugin(name, installed_dir)
    if current_info is None:
        logger.warning(f"Plugin '{name}' is not installed")
        return None

    # Re-install from the original source
    logger.info(f"Updating plugin '{name}' from {current_info.source}")
    return install_plugin(
        source=current_info.source,
        ref=None,  # Get latest (don't use pinned ref)
        repo_path=current_info.repo_path,
        installed_dir=installed_dir,
        force=True,
    )
