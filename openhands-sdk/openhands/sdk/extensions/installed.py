"""Installed extension management.

This module provides the generic infrastructure for managing installed extensions
(plugins, skills, etc.) in a user's home directory.

The installed extensions directory structure follows this pattern::

    ~/.openhands/{type}/installed/
    ├── extension-name-1/
    │   └── ...
    ├── extension-name-2/
    │   └── ...
    └── .installed.json  # Metadata about installed extensions
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from openhands.sdk.extensions.fetch import fetch_extension_with_resolution
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_METADATA_FILENAME = ".installed.json"


class InstalledExtensionInfo(BaseModel):
    """Common metadata for any installed extension.

    This is the base class for tracking metadata about installed extensions.
    Specific extension types (plugins, skills) can extend this with additional
    fields while inheriting the common installation tracking fields.
    """

    name: str = Field(description="Extension name/identifier")
    description: str = Field(default="", description="Extension description")
    enabled: bool = Field(default=True, description="Whether the extension is enabled")
    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None,
        description="Resolved git commit SHA (for version pinning)",
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )
    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the extension is installed")


class InstalledExtensionMetadata[InfoT: InstalledExtensionInfo](BaseModel):
    """Metadata file for tracking installed extensions.

    This class manages the .installed.json file that tracks all installed
    extensions in a directory.
    """

    items: dict[str, InfoT] = Field(
        default_factory=dict,
        description="Map of extension name to installation info",
    )

    @classmethod
    def get_path(cls, installed_dir: Path) -> Path:
        """Get the metadata file path for the given installed directory."""
        return installed_dir / _METADATA_FILENAME

    @classmethod
    def load_from_dir(
        cls,
        installed_dir: Path,
        info_type: type[InfoT],
    ) -> InstalledExtensionMetadata[InfoT]:
        """Load metadata from the installed directory.

        Args:
            installed_dir: Directory containing .installed.json
            info_type: The Pydantic model class for individual info entries

        Returns:
            Loaded metadata instance, or empty metadata if file doesn't exist.
        """
        metadata_path = cls.get_path(installed_dir)
        if not metadata_path.exists():
            return cls()
        try:
            with open(metadata_path) as f:
                data = json.load(f)
            # Support both new format ("items") and legacy formats ("plugins", "skills")
            items_data = data.get("items") or data.get("plugins") or data.get("skills")
            if items_data is None:
                items_data = {}
            # Validate each item with the specific info type
            items = {
                name: info_type.model_validate(info_data)
                for name, info_data in items_data.items()
            }
            return cls(items=items)
        except Exception as e:
            logger.warning(f"Failed to load installed extensions metadata: {e}")
            return cls()

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed directory."""
        metadata_path = self.get_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize items using model_dump for each
        data = {"items": {name: info.model_dump() for name, info in self.items.items()}}
        with open(metadata_path, "w") as f:
            json.dump(data, f, indent=2)


class InstalledExtensionManager[ItemT, InfoT: InstalledExtensionInfo]:
    """Generic manager for installed extensions.

    This class provides all the common operations for managing installed
    extensions: install, uninstall, list, load, enable, disable, update.

    Type Parameters:
        ItemT: The loaded extension type (e.g., Plugin, Skill)
        InfoT: The installation info type (must extend InstalledExtensionInfo)

    Example:
        >>> manager = InstalledExtensionManager[Plugin, InstalledPluginInfo](
        ...     default_dir=Path.home() / ".openhands" / "plugins" / "installed",
        ...     validate_name=validate_plugin_name,
        ...     load_item=Plugin.load,
        ...     create_info=InstalledPluginInfo.from_plugin,
        ...     info_type=InstalledPluginInfo,
        ... )
        >>> info = manager.install("github:owner/my-plugin")
    """

    def __init__(
        self,
        default_dir: Path,
        validate_name: Callable[[str], None],
        load_item: Callable[[Path], ItemT],
        create_info: Callable[[ItemT, str, str | None, str | None, Path], InfoT],
        info_type: type[InfoT],
        get_item_name: Callable[[ItemT], str] | None = None,
    ) -> None:
        """Initialize the extension manager.

        Args:
            default_dir: Default directory for installed extensions.
            validate_name: Function to validate extension names (raises on invalid).
            load_item: Function to load an extension from a path.
            create_info: Function to create InstalledInfo from a loaded item.
                Signature: (item, source, resolved_ref, repo_path, path) -> InfoT
            info_type: The Pydantic model class for installation info.
            get_item_name: Optional function to get name from loaded item.
                If None, assumes item has a .name attribute.
        """
        self._default_dir = default_dir
        self._validate_name = validate_name
        self._load_item = load_item
        self._create_info = create_info
        self._info_type = info_type
        self._get_item_name = get_item_name or (lambda item: item.name)  # type: ignore[attr-defined]

    def _resolve_dir(self, installed_dir: Path | None) -> Path:
        """Return installed_dir or the default if None."""
        return installed_dir if installed_dir is not None else self._default_dir

    def _load_metadata(self, installed_dir: Path) -> InstalledExtensionMetadata[InfoT]:
        """Load metadata from directory."""
        return InstalledExtensionMetadata.load_from_dir(installed_dir, self._info_type)

    def install(
        self,
        source: str,
        ref: str | None = None,
        repo_path: str | None = None,
        installed_dir: Path | None = None,
        force: bool = False,
    ) -> InfoT:
        """Install an extension from a source.

        Fetches the extension from the source, copies it to the installed
        directory, and records the installation metadata.

        Args:
            source: Extension source (GitHub shorthand, git URL, or local path).
            ref: Optional branch, tag, or commit to install.
            repo_path: Subdirectory path within the repository (for monorepos).
            installed_dir: Directory for installed extensions. Uses default if None.
            force: If True, overwrite existing installation.

        Returns:
            Installation info for the installed extension.

        Raises:
            ExtensionFetchError: If fetching the extension fails.
            FileExistsError: If extension is already installed and force=False.
            ValueError: If the extension name is invalid.
        """
        installed_dir = self._resolve_dir(installed_dir)

        logger.info(f"Fetching extension from {source}")
        fetched_path, resolved_ref = fetch_extension_with_resolution(
            source=source,
            ref=ref,
            repo_path=repo_path,
            update=True,
        )

        item = self._load_item(fetched_path)
        item_name = self._get_item_name(item)
        self._validate_name(item_name)

        install_path = installed_dir / item_name
        if install_path.exists() and not force:
            raise FileExistsError(
                f"Extension '{item_name}' is already installed at {install_path}. "
                "Use force=True to overwrite."
            )

        if install_path.exists():
            logger.info(f"Removing existing installation of '{item_name}'")
            shutil.rmtree(install_path)

        logger.info(f"Installing extension '{item_name}' to {install_path}")
        installed_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fetched_path, install_path)

        info = self._create_info(item, source, resolved_ref, repo_path, install_path)

        metadata = self._load_metadata(installed_dir)
        existing_info = metadata.items.get(item_name)
        if existing_info is not None:
            # Preserve enabled state from previous installation
            info.enabled = existing_info.enabled
        metadata.items[item_name] = info
        metadata.save_to_dir(installed_dir)

        logger.info(f"Successfully installed extension '{item_name}'")
        return info

    def uninstall(
        self,
        name: str,
        installed_dir: Path | None = None,
    ) -> bool:
        """Uninstall an extension by name.

        Only extensions tracked in the metadata file can be uninstalled.

        Args:
            name: Name of the extension to uninstall.
            installed_dir: Directory for installed extensions. Uses default if None.

        Returns:
            True if uninstalled successfully, False if not found.
        """
        self._validate_name(name)
        installed_dir = self._resolve_dir(installed_dir)

        metadata = self._load_metadata(installed_dir)
        if name not in metadata.items:
            logger.warning(f"Extension '{name}' is not tracked in metadata")
            return False

        extension_path = installed_dir / name
        if extension_path.exists():
            logger.info(f"Removing extension directory: {extension_path}")
            shutil.rmtree(extension_path)

        del metadata.items[name]
        metadata.save_to_dir(installed_dir)

        logger.info(f"Successfully uninstalled extension '{name}'")
        return True

    def list_installed(
        self,
        installed_dir: Path | None = None,
    ) -> list[InfoT]:
        """List all installed extensions.

        This function is self-healing: it may update the metadata file to remove
        entries whose directories were deleted, and to add entries for directories
        that were manually copied into the installed dir.

        Args:
            installed_dir: Directory for installed extensions. Uses default if None.

        Returns:
            List of installation info for each installed extension.
        """
        installed_dir = self._resolve_dir(installed_dir)

        if not installed_dir.exists():
            return []

        metadata = self._load_metadata(installed_dir)

        valid_items, tracked_changed = self._validate_tracked(metadata, installed_dir)
        discovered, discovered_changed = self._discover_untracked(
            metadata, installed_dir
        )

        if tracked_changed or discovered_changed:
            metadata.save_to_dir(installed_dir)

        return valid_items + discovered

    def load_installed(
        self,
        installed_dir: Path | None = None,
    ) -> list[ItemT]:
        """Load all installed and enabled extensions.

        Args:
            installed_dir: Directory for installed extensions. Uses default if None.

        Returns:
            List of loaded extension objects.
        """
        installed_dir = self._resolve_dir(installed_dir)

        if not installed_dir.exists():
            return []

        installed_infos = self.list_installed(installed_dir)
        items: list[ItemT] = []

        for info in installed_infos:
            if not info.enabled:
                continue
            item_path = installed_dir / info.name
            if item_path.exists():
                try:
                    items.append(self._load_item(item_path))
                except Exception as e:
                    logger.warning(f"Failed to load extension '{info.name}': {e}")

        return items

    def get(
        self,
        name: str,
        installed_dir: Path | None = None,
    ) -> InfoT | None:
        """Get information about a specific installed extension.

        Args:
            name: Name of the extension to look up.
            installed_dir: Directory for installed extensions. Uses default if None.

        Returns:
            Installation info if the extension is installed, None otherwise.
        """
        self._validate_name(name)
        installed_dir = self._resolve_dir(installed_dir)

        metadata = self._load_metadata(installed_dir)
        info = metadata.items.get(name)

        if info is not None:
            extension_path = installed_dir / name
            if not extension_path.exists():
                return None

        return info

    def enable(
        self,
        name: str,
        installed_dir: Path | None = None,
    ) -> bool:
        """Enable an installed extension."""
        return self._set_enabled(name, True, installed_dir)

    def disable(
        self,
        name: str,
        installed_dir: Path | None = None,
    ) -> bool:
        """Disable an installed extension."""
        return self._set_enabled(name, False, installed_dir)

    def _set_enabled(
        self,
        name: str,
        enabled: bool,
        installed_dir: Path | None,
    ) -> bool:
        """Set the enabled state of an extension."""
        self._validate_name(name)
        installed_dir = self._resolve_dir(installed_dir)

        metadata = self._load_metadata(installed_dir)
        if name not in metadata.items:
            logger.warning(f"Extension '{name}' is not installed")
            return False

        metadata.items[name].enabled = enabled
        metadata.save_to_dir(installed_dir)

        state = "enabled" if enabled else "disabled"
        logger.info(f"Extension '{name}' is now {state}")
        return True

    def update(
        self,
        name: str,
        installed_dir: Path | None = None,
    ) -> InfoT | None:
        """Update an installed extension to the latest version.

        Re-fetches the extension from its original source and reinstalls it.

        Args:
            name: Name of the extension to update.
            installed_dir: Directory for installed extensions. Uses default if None.

        Returns:
            Updated installation info if successful, None if not installed.
        """
        self._validate_name(name)
        installed_dir = self._resolve_dir(installed_dir)

        current_info = self.get(name, installed_dir)
        if current_info is None:
            logger.warning(f"Extension '{name}' is not installed")
            return None

        logger.info(f"Updating extension '{name}' from {current_info.source}")
        return self.install(
            source=current_info.source,
            ref=None,  # Get latest (don't use pinned ref)
            repo_path=current_info.repo_path,
            installed_dir=installed_dir,
            force=True,
        )

    def _validate_tracked(
        self,
        metadata: InstalledExtensionMetadata[InfoT],
        installed_dir: Path,
    ) -> tuple[list[InfoT], bool]:
        """Validate tracked extensions exist on disk.

        Returns:
            Tuple of (valid extensions list, whether metadata was modified).
        """
        valid_items: list[InfoT] = []
        changed = False

        for name in list(metadata.items.keys()):
            try:
                self._validate_name(name)
            except ValueError as e:
                logger.warning(
                    f"Invalid tracked extension name {name!r}, removing: {e}"
                )
                del metadata.items[name]
                changed = True
                continue

            extension_path = installed_dir / name
            if extension_path.exists():
                valid_items.append(metadata.items[name])
            else:
                logger.warning(
                    f"Extension '{name}' directory missing, removing from metadata"
                )
                del metadata.items[name]
                changed = True

        return valid_items, changed

    def _discover_untracked(
        self,
        metadata: InstalledExtensionMetadata[InfoT],
        installed_dir: Path,
    ) -> tuple[list[InfoT], bool]:
        """Discover extension directories not tracked in metadata.

        Returns:
            Tuple of (discovered extensions list, whether metadata was modified).
        """
        discovered: list[InfoT] = []
        changed = False

        for item in installed_dir.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue
            if item.name in metadata.items:
                continue

            try:
                self._validate_name(item.name)
            except ValueError:
                logger.debug(f"Skipping directory with invalid extension name: {item}")
                continue

            try:
                loaded_item = self._load_item(item)
            except Exception as e:
                logger.debug(f"Skipping directory {item}: {e}")
                continue

            item_name = self._get_item_name(loaded_item)
            if item_name != item.name:
                logger.warning(
                    "Skipping extension directory because name doesn't match: "
                    f"dir={item.name!r}, extension={item_name!r}"
                )
                continue

            info = self._create_info(
                loaded_item,
                "local",  # Unknown source, assume local
                None,  # No resolved ref
                None,  # No repo path
                item,
            )
            discovered.append(info)
            metadata.items[item.name] = info
            changed = True
            logger.info(f"Discovered untracked extension: {item_name}")

        return discovered, changed

    @property
    def default_dir(self) -> Path:
        """Get the default installation directory."""
        return self._default_dir
