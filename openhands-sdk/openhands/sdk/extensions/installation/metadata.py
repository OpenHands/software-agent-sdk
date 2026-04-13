from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import ClassVar

from pydantic import BaseModel, Field

from openhands.sdk.extensions.installation.info import InstallationInfo
from openhands.sdk.extensions.installation.interface import (
    InstallationInterface,
)
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class MetadataSession:
    """Context manager that binds ``InstallationMetadata`` to its directory.

    On a clean exit (no exception), the metadata is automatically saved.
    This eliminates the need for callers to manually pair ``load_from_dir``
    and ``save_to_dir``, and guarantees that mutations are persisted.

    Use via ``InstallationMetadata.open(installed_dir)``.
    """

    def __init__(
        self,
        installed_dir: Path,
        metadata: InstallationMetadata,
        interface: InstallationInterface | None = None,
    ) -> None:
        self.installed_dir = installed_dir
        self.metadata = metadata
        self.interface = interface

    @property
    def extensions(self) -> dict[str, InstallationInfo]:
        return self.metadata.extensions

    def sync(self) -> list[InstallationInfo]:
        """Reconcile metadata with what is actually on disk.

        Prunes stale tracked entries whose directories are missing and
        discovers untracked extension directories.  Does **not** save —
        the enclosing ``with`` block handles persistence on exit.

        Requires that an ``InstallationInterface`` was provided when the
        session was created (via ``InstallationMetadata.open(..., interface=...)``).

        Returns:
            Combined list of valid tracked and newly discovered extensions.
        """
        assert self.interface is not None, (
            "sync() requires an InstallationInterface; "
            "pass interface= to InstallationMetadata.open()"
        )
        valid = self.metadata.validate_tracked(self.installed_dir)
        discovered = self.metadata.discover_untracked(
            self.installed_dir, self.interface
        )
        return valid + discovered

    def __enter__(self) -> MetadataSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.metadata.save_to_dir(self.installed_dir)


class InstallationMetadata(BaseModel):
    """Metadata file for tracking installed extensions.

    Typically used via the ``open()`` context manager, which loads the
    metadata, yields a ``MetadataSession``, and auto-saves on exit::

        with InstallationMetadata.open(installed_dir) as session:
            session.extensions["my-ext"] = info
        # saved automatically
    """

    extensions: dict[str, InstallationInfo] = Field(
        default_factory=dict,
        description="Map from extension name to extension installation info",
    )

    metadata_filename: ClassVar[str] = ".installed.json"

    @classmethod
    def open(
        cls,
        installed_dir: Path,
        *,
        interface: InstallationInterface | None = None,
    ) -> MetadataSession:
        """Load metadata and return a session that auto-saves on exit.

        Args:
            installed_dir: Root directory where extensions are installed.
            interface: Optional installation interface, required if the
                session will call ``sync()``.
        """
        return MetadataSession(
            installed_dir, cls.load_from_dir(installed_dir), interface
        )

    @classmethod
    def get_metadata_path(cls, installed_dir: Path) -> Path:
        """Get the metadata file path for the installed extension directory."""
        return installed_dir / cls.metadata_filename

    @classmethod
    def load_from_dir(cls, installed_dir: Path) -> InstallationMetadata:
        """Load metadata from the installed extensions directory."""
        metadata_path = cls.get_metadata_path(installed_dir)
        if not metadata_path.exists():
            return cls()

        try:
            with metadata_path.open() as f:
            return cls.model_validate(data)
            return cls.model_validate_json(data)

        except Exception as e:
            logger.warning(f"Failed to load installed extension metadata: {e}")
            return cls()

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed extensions directory."""
        metadata_path = self.get_metadata_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w") as f:
            json.dump(self.model_dump_json(), f, indent=2)

    def validate_tracked(self, installed_dir: Path) -> list[InstallationInfo]:
        """Validate tracked extensions exist on disk.

        Removes entries with invalid names or missing directories from
        ``self.extensions`` in place.

        Returns:
            List of extensions that are still valid.
        """
        valid_extensions: list[InstallationInfo] = []

        # Iterate over a snapshot because we mutate during the loop.
        for name, info in list(self.extensions.items()):
            try:
                validate_extension_name(name)
            except ValueError as e:
                logger.warning(
                    f"Invalid tracked extension name {name!r}, removing: {e}"
                )
                del self.extensions[name]
                continue

            extension_path = installed_dir / name
            if extension_path.exists():
                valid_extensions.append(info)
            else:
                logger.warning(
                    f"Extension {name} directory missing, removing from metadata"
                )
                del self.extensions[name]

        return valid_extensions

    def discover_untracked(
        self,
        installed_dir: Path,
        installation_interface: InstallationInterface,
    ) -> list[InstallationInfo]:
        """Discover extension directories not tracked by the metadata.

        Adds newly found extensions to ``self.extensions`` in place.

        Returns:
            List of newly discovered extensions.
        """
        discovered: list[InstallationInfo] = []

        for item in installed_dir.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue

            if item.name in self.extensions:
                continue

            try:
                validate_extension_name(item.name)
            except ValueError:
                logger.debug(f"Skipping directory with invalid extension name: {item}")
                continue

            try:
                extension = installation_interface.load_from_dir(item)
            except Exception as e:
                logger.debug(f"Skipping directory {item}: {e}")
                continue

            if extension.name != item.name:
                logger.warning(
                    "Skipping extension directory because manifest name"
                    " doesn't match directory name:"
                    f" dir={item.name!r}, manifest={extension.name!r}"
                )
                continue

            info = InstallationInfo.from_extension(
                extension, source="local", install_path=item
            )

            discovered.append(info)
            self.extensions[item.name] = info
            logger.info(f"Discovered untracked extension: {extension.name}")

        return discovered
