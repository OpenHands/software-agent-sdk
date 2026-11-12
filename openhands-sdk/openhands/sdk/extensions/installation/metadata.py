from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Final

from filelock import BaseFileLock, FileLock, Timeout
from pydantic import BaseModel, Field, model_validator

from openhands.sdk.extensions.installation.info import InstallationInfo
from openhands.sdk.extensions.installation.interface import (
    InstallationInterface,
)
from openhands.sdk.extensions.installation.utils import validate_extension_name
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.files import atomic_write_text, read_text_retrying


logger = get_logger(__name__)

_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0


def _running_as_root() -> bool:
    return sys.platform != "win32" and os.geteuid() == 0


def _give_lock_file_to_directory_owner(lock_path: Path) -> None:
    """Make the installation directory's owner own the lock file root holds.

    A root process (an agent-server container, say) sharing the directory with
    its user would otherwise leave a root-owned lock file that user can never
    open again. The file is changed through a descriptor opened without
    following symlinks, and only if it is a regular file with a single link,
    so the directory's owner can't redirect the change to another file. Best
    effort: a failure is logged and the session goes on.
    """
    if sys.platform == "win32":
        return
    try:
        directory = lock_path.parent.stat()
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            current = os.fstat(fd)
            if (
                stat.S_ISREG(current.st_mode)
                and current.st_nlink == 1
                and (current.st_uid, current.st_gid)
                != (directory.st_uid, directory.st_gid)
            ):
                os.fchown(fd, directory.st_uid, directory.st_gid)
        finally:
            os.close(fd)
    except OSError as e:
        logger.warning(f"Could not give {lock_path} to its directory's owner: {e}")


class InstallationMetadataLoadError(Exception):
    """An existing metadata file could not be read or parsed.

    A write session raises this instead of starting from empty metadata,
    because saving that fallback would overwrite every recorded source and
    enabled state. The file is left unchanged.
    """

    def __init__(self, metadata_path: Path, cause: BaseException) -> None:
        super().__init__(
            f"Could not load installation metadata at {metadata_path} "
            f"({type(cause).__name__}); the file was left unchanged"
        )
        self.metadata_path = metadata_path


class InstallationMetadataLockTimeoutError(Exception):
    """The installation metadata lock was not acquired in time.

    Another operation (an install, update, uninstall or listing) held it for
    the whole timeout. Retrying later can succeed. Deliberately not a
    ``TimeoutError``: that is an ``OSError``, which callers already map to
    unrelated errors (an invalid extension, for one).
    """

    def __init__(self, installed_dir: Path, timeout: float) -> None:
        super().__init__(
            f"Timed out after {timeout}s waiting for the installation "
            f"metadata lock in {installed_dir}"
        )
        self.installed_dir = installed_dir


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
        lock: BaseFileLock | None = None,
    ) -> None:
        self.installed_dir = installed_dir
        self.metadata = metadata
        self.interface = interface
        self.lock = lock

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
        try:
            if exc_type is None:
                self.metadata.save_to_dir(self.installed_dir)
        finally:
            if self.lock is not None:
                self.lock.release()


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
    _LEGACY_KEYS: ClassVar[tuple[str, ...]] = ("plugins", "skills")

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_keys(cls, data: Any) -> Any:
        """Migrate old ``plugins`` / ``skills`` keys into ``extensions``.

        Legacy entries are merged into the existing ``extensions`` dict
        (if any).  Explicit ``extensions`` entries win on key conflicts.
        """
        if not isinstance(data, dict):
            return data
        merged: dict[str, Any] = {}
        for legacy_key in cls._LEGACY_KEYS:
            if legacy_key in data:
                logger.warning(
                    "Migrating legacy %r key to 'extensions'",
                    legacy_key,
                )
                merged.update(data.pop(legacy_key))
        if merged:
            merged.update(data.get("extensions") or {})
            data["extensions"] = merged
        return data

    @classmethod
    def open(
        cls,
        installed_dir: Path,
        *,
        interface: InstallationInterface | None = None,
    ) -> MetadataSession:
        """Load metadata and return a session that auto-saves on exit.

        Acquires a file lock on ``installed_dir`` before loading and holds it
        until the session exits, so a load-modify-save cycle cannot interleave
        with another one (another thread or process). Always use the result in
        a ``with`` block, and do not nest sessions on the same directory.

        Args:
            installed_dir: Root directory where extensions are installed.
            interface: Optional installation interface, required if the
                session will call ``sync()``.

        Raises:
            InstallationMetadataLockTimeoutError: Another session held the
                lock for the whole timeout.
            InstallationMetadataLoadError: The metadata file exists but cannot
                be read or parsed. The lock is released and the file is left
                unchanged.
        """
        installed_dir.mkdir(parents=True, exist_ok=True)
        lock = cls._acquire_lock(installed_dir)
        try:
            return MetadataSession(
                installed_dir,
                cls._load_for_session(installed_dir),
                interface,
                lock=lock,
            )
        except BaseException:
            lock.release()
            raise

    @staticmethod
    def _acquire_lock(installed_dir: Path) -> BaseFileLock:
        lock_path = installed_dir / ".metadata.lock"
        lock = FileLock(lock_path)
        try:
            lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS)
        except Timeout as e:
            logger.error(
                f"Failed to acquire installation metadata lock in "
                f"{_LOCK_TIMEOUT_SECONDS}s: {installed_dir}"
            )
            raise InstallationMetadataLockTimeoutError(
                installed_dir, _LOCK_TIMEOUT_SECONDS
            ) from e
        if _running_as_root():
            _give_lock_file_to_directory_owner(lock_path)
        return lock

    @classmethod
    def get_metadata_path(cls, installed_dir: Path) -> Path:
        """Get the metadata file path for the installed extension directory."""
        return installed_dir / cls.metadata_filename

    @classmethod
    def load_from_dir(cls, installed_dir: Path) -> InstallationMetadata:
        """Load metadata from the installed extensions directory.

        For read-only callers. Takes no lock: a save swaps in a complete file,
        so a concurrent read sees the old or the new version, never a partial
        one. On Windows, a read that meets the swap is retried. A file that
        cannot be read or parsed loads as empty.
        """
        metadata_path = cls.get_metadata_path(installed_dir)
        if not metadata_path.exists():
            return cls()

        try:
            return cls.model_validate_json(read_text_retrying(metadata_path))
        except Exception as e:
            logger.warning(f"Failed to load installed extension metadata: {e}")
            return cls()

    @classmethod
    def _load_for_session(cls, installed_dir: Path) -> InstallationMetadata:
        """Load metadata for a write session.

        Unlike ``load_from_dir``, a file that exists but cannot be read or
        parsed raises instead of loading as empty, since the session would
        save that empty fallback over it. A missing file still loads as empty.
        """
        metadata_path = cls.get_metadata_path(installed_dir)
        try:
            text = metadata_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        except (OSError, UnicodeDecodeError) as e:
            raise InstallationMetadataLoadError(metadata_path, e) from e
        try:
            return cls.model_validate_json(text)
        except Exception as e:
            raise InstallationMetadataLoadError(metadata_path, e) from e

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed extensions directory.

        An existing regular file keeps its permission bits, and, when root saves
        it, its owner. A new file gets its mode from the umask.
        """
        metadata_path = self.get_metadata_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        mode: int | None = None
        owner: tuple[int, int] | None = None
        try:
            existing = metadata_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISREG(existing.st_mode):
                mode = stat.S_IMODE(existing.st_mode)
                if _running_as_root():
                    owner = (existing.st_uid, existing.st_gid)
        atomic_write_text(
            metadata_path, self.model_dump_json(indent=2), mode=mode, owner=owner
        )

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

        for item in sorted(installed_dir.iterdir()):
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
