from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol

from pydantic import BaseModel, Field

from openhands.sdk.extensions.installation.info import InstalledExtensionInfoBaseClass
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class ExtensionProtocol(Protocol):
    name: str


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

    def discover_untracked[T: ExtensionProtocol](
        self,
        installed_dir: Path,
        extension_loader: Callable[[Path], T],
        info_extractor: Callable[[T], InfoT],
    ) -> tuple[list[InfoT], bool]:
        """Discover extension directories not tracked by the metadata.

        Returns:
            Tuple of (discovered extensions list, whether metadata was modified).
        """
        discovered: list[InfoT] = []
        changed = False

        for item in installed_dir.iterdir():
            # Focus only on non-hidden directories
            if not item.is_dir() or item.name.startswith("."):
                continue

            # Ignore already-tracked extensions
            if item.name in self.extensions:
                continue

            # Ignore directories with the wrong naming scheme
            try:
                validate_extension_name(item.name)
            except ValueError:
                logger.debug(f"Skipping directory with invalid extension name: {item}")

            # Try to load the directory as the indicated extension
            try:
                extension = extension_loader(item)
            except Exception as e:
                logger.debug(f"Skipping directory {item}: {e}")
                continue

            if extension.name != item.name:
                logger.warning(
                    "Skipping extension directory because manifest name doesn't match "
                    f"directory name: dir={item.name!r}, manifest={extension.name!r}"
                )
                continue

            info = info_extractor(extension)
            info.source = "local"
            info.installed_at = datetime.now(UTC).isoformat()
            info.install_path = str(item)

            discovered.append(info)
            self.extensions[item.name] = info
            changed = True
            logger.info(f"Discovered untracked extension: {extension.name}")

        return discovered, changed

    def sync_installed[T: ExtensionProtocol](
        self,
        installed_dir: Path,
        extension_loader: Callable[[Path], T],
        info_extractor: Callable[[T], InfoT],
    ) -> list[InfoT]:
        valid_extensions, tracked_changed = self.validate_tracked(installed_dir)
        discovered, discovered_changed = self.discover_untracked(
            installed_dir, extension_loader, info_extractor
        )

        if tracked_changed or discovered_changed:
            self.save_to_dir(installed_dir)

        return valid_extensions + discovered
