from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from openhands.sdk.extensions.fetch import fetch_with_resolution
from openhands.sdk.extensions.installation.info import InstallationInfo
from openhands.sdk.extensions.installation.interface import (
    ExtensionProtocol,
    InstallationInterface,
)
from openhands.sdk.extensions.installation.metadata import (
    InstallationMetadata,
    MetadataSession,
)
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "extensions"


@dataclass
class InstallationManager[T: ExtensionProtocol]:
    """Generic manager for installing, tracking, and loading extensions.

    Parameterised by any type ``T`` that satisfies ``ExtensionProtocol``.
    The companion ``InstallationInterface[T]`` tells the manager how to
    load ``T`` from a directory on disk; everything else (fetching, copying,
    metadata bookkeeping) is handled generically.

    Attributes:
        installation_dir: Root directory where extensions are installed.
        installation_interface: Knows how to load ``T`` from a directory.
    """

    installation_dir: Path
    installation_interface: InstallationInterface[T]

    def __post_init__(self) -> None:
        self.installation_dir = self.installation_dir.resolve()

    @property
    def metadata_session(self) -> MetadataSession:
        """Open a metadata session bound to this manager's dir and interface."""
        return InstallationMetadata.open(
            self.installation_dir, interface=self.installation_interface
        )

    def install(
        self,
        source: str | Path,
        ref: str | None = None,
        repo_path: str | None = None,
        force: bool = False,
    ) -> InstallationInfo:
        """Install an extension from a source.

        Fetches the extension from the source, copies it to the installation
        directory, and records installation metadata.  When ``force=True``
        overwrites an existing installation, the previous ``enabled`` state is
        preserved.

        Args:
            source: Extension source — can be a ``"github:owner/repo"``
                shorthand, any git URL, or a local filesystem path.
            ref: Optional branch, tag, or commit to install.
            repo_path: Subdirectory path within the repository (for monorepos).
            force: If True, overwrite existing installation.  If False, raise
                an error if the extension is already installed.

        Returns:
            InstallationInfo with details about the installation.

        Raises:
            ExtensionFetchError: If fetching the extension fails.
            FileExistsError: If extension is already installed and force=False.
            ValueError: If the extension name is invalid.
        """
        if isinstance(source, Path):
            source = str(source)

        logger.info(f"Fetching extension from {source}")
        fetched_path, resolved_ref = fetch_with_resolution(
            source=source,
            cache_dir=DEFAULT_CACHE_DIR,
            ref=ref,
            repo_path=repo_path,
            update=True,
        )

        extension = self.installation_interface.load_from_dir(fetched_path)
        validate_extension_name(extension.name)

        install_path = self.installation_dir / extension.name
        if install_path.exists() and not force:
            raise FileExistsError(
                f"Extension '{extension.name}' is already installed"
                f" at {install_path}. Use force=True to overwrite."
            )

        if install_path.exists():
            logger.info(f"Removing existing installation of '{extension.name}'")
            shutil.rmtree(install_path)

        logger.info(f"Installing extension '{extension.name}' to {install_path}")
        self.installation_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fetched_path, install_path)

        info = InstallationInfo.from_extension(
            extension,
            source=source,
            install_path=install_path,
            resolved_ref=resolved_ref,
            repo_path=repo_path,
        )

        with self.metadata_session as session:
            existing = session.extensions.get(extension.name)
            if existing is not None:
                info.enabled = existing.enabled
            session.extensions[extension.name] = info

        logger.info(
            f"Successfully installed extension '{extension.name}' v{info.version}"
        )
        return info

    def uninstall(self, name: str) -> bool:
        """Uninstall an extension by name.

        Only extensions tracked in the metadata can be uninstalled.  This
        prevents accidentally deleting arbitrary directories that happen to
        exist inside the installation directory.  If the extension's directory
        has already been removed, the metadata entry is still cleaned up.

        Args:
            name: Name of the extension to uninstall.

        Returns:
            True if the extension was uninstalled, False if it wasn't tracked.

        Raises:
            ValueError: If *name* is not valid kebab-case.
        """
        validate_extension_name(name)

        with self.metadata_session as session:
            if name not in session.extensions:
                logger.warning(f"Extension '{name}' is not installed")
                return False

            extension_path = self.installation_dir / name
            if extension_path.exists():
                logger.info(f"Uninstalling extension '{name}' from {extension_path}")
                shutil.rmtree(extension_path)
            else:
                logger.warning(
                    f"Extension '{name}' was tracked but {extension_path} is missing"
                )

            del session.extensions[name]

        logger.info(f"Successfully uninstalled extension '{name}'")
        return True

    def _set_enabled(
        self,
        name: str,
        enabled: bool,
    ) -> bool:
        """Set the enabled state of an installed extension.

        Syncs metadata before checking, so stale or untracked entries are
        reconciled first.  Returns False if the extension is not installed
        or its directory is missing.
        """
        validate_extension_name(name)

        if not self.installation_dir.exists():
            logger.warning(
                f"Installation directory does not exist: {self.installation_dir}"
            )
            return False

        with self.metadata_session as session:
            session.sync()

            info = session.extensions.get(name)
            if info is None:
                logger.warning(f"Extension '{name}' is not installed")
                return False

            extension_path = self.installation_dir / name
            if not extension_path.exists():
                logger.warning(
                    f"Extension '{name}' was tracked but {extension_path} is missing"
                )
                return False

            if info.enabled == enabled:
                return True

            info.enabled = enabled
            session.extensions[name] = info

        state = "enabled" if enabled else "disabled"
        logger.info(f"Successfully {state} extension '{name}'")
        return True

    def enable(self, name: str) -> bool:
        """Enable an installed extension by name."""
        return self._set_enabled(name, True)

    def disable(self, name: str) -> bool:
        """Disable an installed extension by name."""
        return self._set_enabled(name, False)

    def list_installed(self) -> list[InstallationInfo]:
        """List all installed extensions.

        Self-healing: the metadata file is updated to remove entries whose
        directories have been deleted and to add entries for extension
        directories that were manually copied into the installation directory.

        Returns:
            List of InstallationInfo for each installed extension.
        """
        if not self.installation_dir.exists():
            return []

        with self.metadata_session as session:
            return session.sync()

    def load_installed(self) -> list[T]:
        """Load all enabled extensions as ``T`` objects.

        Calls ``list_installed()`` first (which syncs metadata), then loads
        each enabled extension via the installation interface.  Disabled
        extensions are skipped.

        Returns:
            List of loaded extension objects of type ``T``.
        """
        if not self.installation_dir.exists():
            return []

        extensions: list[T] = []

        for info in self.list_installed():
            if not info.enabled:
                continue

            extension_path = self.installation_dir / info.name
            if extension_path.exists():
                extension = self.installation_interface.load_from_dir(extension_path)
                extensions.append(extension)

        return extensions

    def get(self, name: str) -> InstallationInfo | None:
        """Get information about a specific installed extension.

        Returns ``None`` if the extension is not tracked in metadata or if
        its directory no longer exists on disk.

        Args:
            name: Name of the extension to look up.

        Returns:
            InstallationInfo if the extension is installed, None otherwise.

        Raises:
            ValueError: If *name* is not valid kebab-case.
        """
        validate_extension_name(name)

        metadata = InstallationMetadata.load_from_dir(self.installation_dir)
        info = metadata.extensions.get(name)

        if info is not None:
            extension_path = self.installation_dir / name
            if not extension_path.exists():
                return None

        return info

    def update(self, name: str) -> InstallationInfo | None:
        """Update an installed extension to the latest version.

        Re-fetches the extension from its original source with ``ref=None``
        (i.e. the latest available) and force-reinstalls it.  The previous
        ``enabled`` state is preserved because ``install(force=True)``
        carries it over.

        Args:
            name: Name of the extension to update.

        Returns:
            Updated InstallationInfo if successful, None if the extension is
            not installed.

        Raises:
            ExtensionFetchError: If fetching the updated extension fails.
            ValueError: If *name* is not valid kebab-case.
        """
        validate_extension_name(name)

        current_info = self.get(name)
        if current_info is None:
            logger.warning(f"Extension {name} not installed")
            return None

        logger.info(f"Updating extension {name} from {current_info.source}")
        return self.install(
            source=current_info.source,
            ref=None,
            repo_path=current_info.repo_path,
            force=True,
        )
