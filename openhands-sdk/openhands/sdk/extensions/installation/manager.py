from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openhands.sdk.extensions.installation.info import InstalledExtensionInfo
from openhands.sdk.extensions.installation.interface import (
    InstallableExtensionInterface,
    InstallableExtensionProtocol,
)
from openhands.sdk.extensions.installation.metadata import InstalledExtensionMetadata
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


@dataclass
class InstalledExtensionManager[T: InstallableExtensionProtocol]:
    """Manages installed extensions."""

    installation_dir: Path
    installation_interface: InstallableExtensionInterface[T]

    def install(
        self,
        source: str,
        ref: str | None = None,
        repo_path: str | None = None,
        force: bool = False,
    ) -> InstalledExtensionInfo:
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

    def list_installed(self) -> list[InstalledExtensionInfo]:
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

    def get(self, name: str) -> InstalledExtensionInfo | None:
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

    def update(self, name: str) -> InstalledExtensionInfo | None:
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
