from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from openhands.sdk.extensions.installation.info import InstalledExtensionInfoBaseClass
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


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
