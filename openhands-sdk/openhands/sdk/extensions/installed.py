from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel, Field

from openhands.sdk.extensions.utils import validate_extension_name
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class InstalledExtensionInfoBaseClass[T](ABC, BaseModel):
    """Base class for information about an installed extension.

    Linked to extensions by the installed extension metadata.
    """

    name: str = Field(description="Extension name")
    version: str = Field(default="1.0.0", description="Extension version")

    enabled: bool = Field(default=True, description="Whether the extension is enabled")

    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None, description="Resolved git commit SHA (for version pinning)"
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )

    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the extension is installed")

    @classmethod
    @abstractmethod
    def from_extension(
        cls: type[Self],
        extension: T,
        source: str,
        resolved_ref: str | None,
        repo_path: str | None,
        install_path: Path,
    ) -> Self:
        """Create installed extension info from a loaded skill."""
        raise NotImplementedError()


class InstalledExtensionMetadata[InfoT: InstalledExtensionInfoBaseClass](BaseModel):
    """Metadata file for tracking installed extensions."""

    extensions: dict[str, InfoT] = Field(
        default_factory=dict,
        description="Map from extension name to extension installation info",
    )

    metadata_filename: ClassVar[str] = ".installed.json"

    @classmethod
    def get_metadata_path(cls, installed_dir: Path) -> Path:
        """Get the metadata file path for the installed extension directory."""
        return installed_dir / cls.metadata_filename

    @classmethod
    def load_from_dir(cls, installed_dir: Path) -> InstalledExtensionMetadata[InfoT]:
        """Load metadata from the installed extensions directory."""
        metadata_path = cls.get_metadata_path(installed_dir)
        if not metadata_path.exists():
            return cls()

        try:
            with metadata_path.open() as f:
                data = json.load(f)
            return cls.model_validate(data)

        except Exception as e:
            logger.warning(f"Failed to load installed extension metadata: {e}")
            return cls()

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed extensions directory."""
        metadata_path = self.get_metadata_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w") as f:
            json.dump(self.model_dump(), f, indent=2)

    def validate_tracked(self, installed_dir: Path) -> tuple[list[InfoT], bool]:
        """Validate tracked extensions exist on disk.

        Removes any extension with an invalid name or missing directory.

        Returns:
            Tuple of (valid extensions list, whether metadata was modified).
        """
        valid_extensions: list[InfoT] = []
        changed = False

        # We cannot iterate directly over the extensions because we'll be removing
        # invalid extensions as we go.
        for name, info in list(self.extensions.items()):
            # Check the extension name
            try:
                validate_extension_name(name)
            except ValueError as e:
                logger.warning(
                    f"Invalid tracked extension name {name!r}, removing: {e}"
                )
                del self.extensions[name]
                changed = True
                continue

            # Check the extension installation
            extension_path = installed_dir / name
            if extension_path.exists():
                valid_extensions.append(info)
            else:
                logger.warning(
                    f"Extension {name} directory missing, removing from metadata"
                )
                del self.extensions[name]
                changed = True

        return valid_extensions, changed


class InstalledExtensionManager[T, InfoT: InstalledExtensionInfoBaseClass](BaseModel):
    """Manages installed extensions."""

    installation_dir: Path = Field(description="Directory for installed extensions.")

    def install(
        self,
        source: str,
        ref: str | None = None,
        repo_path: str | None = None,
        force: bool = False,
    ) -> InfoT:
        """Install an extension from a source.

        Fetches the extensionFrom the source, copies it to the installed extensions
        directory, and records the installation metadata.

        Args:
            source: Extension source - can be:
                - "github:owner/repo" - GitHub shorthand
                - Any git URL (GitHub, GitLab, Bitbucket, etc.)
                - Local path (for development/testing)
            ref: Optional branch, tag, or commit to install.
            repo_path: Subdirectory path within the repository (for monorepos).
            force: If True, overwrite existing installation. If False, raise error
                if extension is already installed.

        Returns:
            InstalledExtensionInfoBaseClass[T] instance with details about the
                installation.

        Raises:
            ExtensionFetchError: If fetching the extension fails.
            FileExistsError: If extension is already installed and force=False.
            ValueError: If the extension manifest is invalid.

        Example:
            >>> info = install("github:owner/my-extension", ref="v1.0.0")
            >>> print(f"Installed {info.name} from {info.source}")
        """

        raise NotImplementedError()

    def uninstall(self, name: str) -> bool:
        """Uninstall an extension by name.

        Only extensions tracked in the installed extensions metadata file can be
        uninstalled. This avoids deleting arbitrary directories in the installed
        extensions directory.

        Args:
            name: Name of the extension to uninstall.

        Returns:
            True if the extension was uninstalled, False if it wasn't installed.

        Example:
            >>> if uninstall("my-extension"):
            ...     print("Extension uninstalled")
            ... else:
            ...     print("Extension was not installed")
        """
        raise NotImplementedError()

    def enable(self, name: str) -> bool:
        """Enable an installed extension by name."""
        raise NotImplementedError()

    def disable(self, name: str) -> bool:
        """Disable an installed extension by name."""
        raise NotImplementedError()

    def list_installed(self) -> list[InfoT]:
        """List all installed extensions.

        This function is self-healing: it may update the installed extensions metadata
        file to remove entries whose directories were deleted, and to add entries for
        extension directories that were manually copied into the installed dir.

        Returns:
            List of InstalledExtensionInfoBaseClass[T] for each installed extension.

        Example:
            >>> for info in list_installed():
            ...     print(f"{info.name} v{info.version}")
        """
        raise NotImplementedError()

    def load_installed(self) -> list[T]:
        """Load all installed extensions.

        Loads extension objects for all extensions in the installed extensions
        directory. This is useful for integrating installed extensions into an agent.

        Returns:
            List of loaded extension objects.

        Example:
            >>> extension = load_installed()
            >>> for ext in extensions:
            ...     print(f"Loaded {ext}")
        """
        raise NotImplementedError()

    def get(self, name: str) -> InfoT | None:
        """Get information about a specific installed extension.

        Args:
            name: Name of the extension to look up.
            installed_dir: Directory for installed extensions.

        Returns:
            InstalledExtensionInfoBaseClass[T] if the extension is installed, None
                otherwise.

        Example:
            >>> info = get("my-extension")
            >>> if info:
            ...     print(f"Installed from {info.source} at {info.installed_at}")
        """
        validate_extension_name(name)

        metadata = InstalledExtensionMetadata.load_from_dir(self.installation_dir)
        info = metadata.extensions.get(name)

        # Verify the extension directory still exists
        if info is not None:
            extension_path = self.installation_dir / name
            if not extension_path.exists():
                return None

        return info

    def update(self, name: str) -> InfoT | None:
        """Update an installed extension to the latest version.

        Re-fetches the extension from its original source and reinstalls it.

        This always updates to the latest version available from the original source
        (i.e., it does not preserve a pinned ref).

        Args:
            name: Name of the extension to update.
            installed_dir: Directory for installed extensions. Defaults to the
                installed_dir instance variable.

        Returns:
            Updated InstalledExtensionInfoBaseClass[T] if successful, None if extension
                not installed.

        Raises:
            ExtensionFetchError: If fetching the updated extension fails.

        Example:
            >>> info = update("my-extension")
            >>> if info:
            ...     print(f"Updated to v{info.version}")
        """
        validate_extension_name(name)

        # Get the current installation info
        current_info = self.get(name)
        if current_info is None:
            logger.warning(f"Extension {name} not installed")
            return None

        # Re-install from the original source
        logger.info(f"Updating extension {name} from {current_info.source}")
        return self.install(
            source=current_info.source,
            ref=None,  # Get the latest (don't use pinned ref)
            repo_path=current_info.repo_path,
            force=True,
        )
