from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class InstalledExtensionInfoBaseClass[T](ABC, BaseModel):
    """Base class for information about an installed extension.

    Linked to extensions by the installed extension metadata.
    """

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


# Cannot precisely specify the relationship between T and InfoT without running into
# higher-kinded types (not supported by any version of Python).

class InstalledExtensionManager[T, InfoT: InstalledExtensionInfoBaseClass]:
    def install(
        self,
        source: str,
        ref: str | None = None,
        repo_path: str | None = None,
        installed_dir: Path | None = None,
        force: bool = False,
    ) -> InfoT:
        raise NotImplementedError()

    def uninstall(self, name: str, installed_dir: Path | None = None) -> bool:
        raise NotImplementedError()

    def enable(self, name: str, installed_dir: Path | None = None) -> bool:
        raise NotImplementedError()

    def disable(self, name: str, installed_dir: Path | None = None) -> bool:
        raise NotImplementedError()

    def list_installed(self, installed_dir: Path | None = None) -> list[InfoT]:
        raise NotImplementedError()

    def load_installed(self, installed_dir: Path | None = None) -> list[T]:
        raise NotImplementedError()

    def get(self, name: str, installed_dir: Path | None = None) -> InfoT | None:
        raise NotImplementedError()

    def update(self, name: str, installed_dir: Path | None = None) -> InfoT | None:
        raise NotImplementedError()
