"""File-based storage implementations for settings and secrets.

Following the same pattern as OpenHands app-server's FileSettingsStore
and FileSecretsStore for consistency.

File locking uses fcntl on Unix and msvcrt on Windows.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from openhands.agent_server.persistence.models import (
    CustomSecret,
    PersistedSettings,
    Secrets,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.cipher import Cipher


# fcntl is Unix-only; on Windows, use msvcrt for file locking
if sys.platform != "win32":
    import fcntl

    msvcrt = None
else:
    fcntl = None  # type: ignore[assignment]
    import msvcrt


if TYPE_CHECKING:
    from openhands.agent_server.config import Config


logger = get_logger(__name__)

# File permission constants (owner read/write only)
_DIR_MODE = stat.S_IRWXU  # 0o700 - rwx------
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600 - rw-------

# Windows reserved filenames (case-insensitive)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)


def _validate_filename(filename: str) -> None:
    """Validate filename to prevent path traversal and injection attacks.

    Raises:
        ValueError: If filename is invalid or potentially dangerous.
    """
    # Check for empty filename (would resolve to parent directory)
    if not filename:
        raise ValueError("filename must not be empty")

    # Check for path separators
    if "/" in filename or "\\" in filename:
        raise ValueError("filename must not contain path separators")

    # Check for leading dots (hidden files, parent directory traversal)
    if filename.startswith("."):
        raise ValueError("filename must not start with '.'")

    # Check for null bytes (null byte injection)
    if "\x00" in filename:
        raise ValueError("filename must not contain null bytes")

    # Check for trailing dots/spaces (Windows path handling issues)
    if filename.endswith(".") or filename.endswith(" "):
        raise ValueError("filename must not end with '.' or space")

    # Check for Windows reserved names (split handles multi-extension files)
    # e.g., "CON.txt.json" -> "CON" not "CON.txt"
    basename = filename.split(".")[0].upper()
    if basename in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"filename '{filename}' uses a reserved name")


def _ensure_secure_directory(path: Path) -> None:
    """Ensure directory exists with secure permissions.

    Creates all parent directories with secure permissions (0o700).
    If it already exists, ensures permissions are correct.
    """
    if not path.exists():
        # Create parents with secure permissions
        current = path
        to_create: list[Path] = []
        while not current.exists():
            to_create.append(current)
            current = current.parent

        for dir_path in reversed(to_create):
            dir_path.mkdir(mode=_DIR_MODE, exist_ok=True)

    # Ensure permissions are correct even if dir already existed
    try:
        path.chmod(_DIR_MODE)
    except OSError as e:
        logger.warning(f"Failed to set permissions on {path}: {e}")


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """Context manager for file-based locking.

    Uses Unix fcntl for exclusive locking to prevent race conditions during
    read-modify-write operations. On Windows, uses msvcrt.locking.
    """
    _ensure_secure_directory(lock_path.parent)

    # Create lock file - use O_RDWR for Windows compatibility with msvcrt
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, _FILE_MODE)
    try:
        if fcntl is not None:
            # Unix: use fcntl for file locking
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            # Windows: use msvcrt for file locking
            # Lock multiple bytes for more reliable locking behavior
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 100)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 100)
        else:
            # This should never happen on standard systems (Unix or Windows)
            # Raise an error rather than silently proceeding without locking,
            # which could cause data corruption from concurrent writes
            raise RuntimeError(
                "File locking not available on this platform. "
                "Concurrent writes may cause data corruption."
            )
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically with secure permissions.

    Uses write-to-temp-then-rename pattern to prevent corruption
    if interrupted. Creates temp file with owner-only permissions from
    the start to prevent race conditions where sensitive data could
    be read before chmod.

    Note:
        The rename operation (Path.replace) is atomic on POSIX systems.
        On Windows, it may not be fully atomic in all edge cases (e.g.,
        concurrent access, network drives), but provides reasonable
        protection against corruption from interrupted writes.
    """
    import uuid

    # Use PID, time, and uuid for unique temp filename to prevent collisions
    # when multiple processes/threads write to the same file concurrently
    unique_suffix = f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    tmp_path = path.with_suffix(unique_suffix)
    # Create file with secure permissions from the start using os.open
    # O_EXCL ensures exclusive creation (fails if file exists)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    fdopen_succeeded = False
    try:
        f = os.fdopen(fd, "w", encoding="utf-8")
        fdopen_succeeded = True
        with f:
            json.dump(data, f, indent=2)
    except Exception:
        # Only close fd manually if os.fdopen() didn't take ownership
        if not fdopen_succeeded:
            try:
                os.close(fd)
            except OSError:
                pass
        # Clean up temp file on error
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    tmp_path.replace(path)  # Atomic on POSIX


# Default storage directory (relative to working directory)
DEFAULT_PERSISTENCE_DIR = Path("workspace/.openhands")


class SettingsStore(ABC):
    """Abstract base class for settings storage."""

    @abstractmethod
    def load(self) -> PersistedSettings | None:
        """Load settings from storage."""

    @abstractmethod
    def save(self, settings: PersistedSettings) -> None:
        """Save settings to storage."""

    @abstractmethod
    def update(
        self, update_fn: Callable[[PersistedSettings], PersistedSettings]
    ) -> PersistedSettings:
        """Atomically update settings with file locking.

        Args:
            update_fn: Function that receives current settings and returns
                updated settings.

        Returns:
            The updated settings after saving.
        """


class SecretsStore(ABC):
    """Abstract base class for secrets storage."""

    @abstractmethod
    def load(self) -> Secrets | None:
        """Load secrets from storage."""

    @abstractmethod
    def save(self, secrets: Secrets) -> None:
        """Save secrets to storage."""

    @abstractmethod
    def get_secret(self, name: str) -> str | None:
        """Get a single secret value by name."""

    @abstractmethod
    def set_secret(self, name: str, value: str, description: str | None = None) -> None:
        """Set a single secret."""

    @abstractmethod
    def delete_secret(self, name: str) -> bool:
        """Delete a secret. Returns True if it existed."""


class FileSettingsStore(SettingsStore):
    """File-based settings storage.

    Stores settings as JSON in a configurable directory.
    Secrets within settings are encrypted using the provided cipher.

    Security features:
        - Files created with owner-only permissions (0o600)
        - Directory created with owner-only permissions (0o700)
        - Atomic writes to prevent corruption
    """

    def __init__(
        self,
        persistence_dir: Path | str,
        cipher: Cipher | None = None,
        filename: str = "settings.json",
    ):
        # Validate filename to prevent path traversal and injection attacks
        _validate_filename(filename)
        self.persistence_dir = Path(persistence_dir)
        self.cipher = cipher
        self.filename = filename
        self._path = self.persistence_dir / filename
        self._lock_path = self.persistence_dir / ".settings.lock"

    def load(self) -> PersistedSettings | None:
        """Load settings from file.

        If a cipher is provided, secrets are decrypted via Pydantic's
        validation context. The cipher is passed to model_validate which
        flows through to field validators using validate_secret().
        """
        if not self._path.exists():
            logger.debug(f"Settings file not found: {self._path}")
            return None

        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # Pass cipher in context for automatic decryption of all secret fields
            # This flows through to field validators using validate_secret()
            context = {"cipher": self.cipher} if self.cipher else None
            return PersistedSettings.model_validate(data, context=context)
        except (PermissionError, OSError) as e:
            # Critical filesystem errors should be re-raised
            logger.error(f"Cannot access settings file: {e}")
            raise
        except json.JSONDecodeError as e:
            # Corrupted file - log and return None to allow recovery
            logger.error(f"Settings file is corrupted: {e}")
            return None
        except Exception:
            # Validation or other errors - log and return None
            logger.error("Failed to load settings", exc_info=True)
            return None

    def save(self, settings: PersistedSettings) -> None:
        """Save settings to file atomically with secure permissions.

        If a cipher is provided, secrets are encrypted via Pydantic's
        serialization context. The cipher is passed to model_dump which
        flows through to field serializers using serialize_secret().

        Warning:
            This method does NOT acquire a file lock. For concurrent-safe
            updates, use :meth:`update` which wraps save() with file locking.
            Direct calls to save() from multiple processes may cause lost updates.

        Warning:
            If no cipher is provided, secrets are stored in plaintext.
            This is logged as a security warning on first save.
        """
        _ensure_secure_directory(self.persistence_dir)

        # Pass cipher in context for automatic encryption of all secret fields
        # This flows through to field serializers using serialize_secret()
        if self.cipher:
            context: dict[str, Any] = {"cipher": self.cipher}
        else:
            context = {"expose_secrets": "plaintext"}
            # Warn about plaintext secret storage (only if secrets exist)
            if settings.llm_api_key_is_set:
                logger.warning(
                    "Saving settings with secrets in PLAINTEXT (no cipher configured). "
                    "Configure OH_SECRET_KEY for production deployments."
                )

        data = settings.model_dump(mode="json", context=context)

        _atomic_write_json(self._path, data)
        logger.debug(f"Settings saved to {self._path}")

    def update(
        self, update_fn: Callable[[PersistedSettings], PersistedSettings]
    ) -> PersistedSettings:
        """Atomically update settings with file locking.

        Uses file locking to prevent concurrent updates from overwriting
        each other. The update function is called within the lock.

        Args:
            update_fn: Function that receives current settings and returns
                updated settings.

        Returns:
            The updated settings after saving.

        Raises:
            RuntimeError: If the settings file exists but cannot be loaded
                (e.g., corrupted JSON, decryption failure). This prevents
                data loss from overwriting existing settings with defaults.
        """
        with _file_lock(self._lock_path):
            settings = self.load()
            if settings is None:
                # File doesn't exist or is empty - safe to use defaults
                if self._path.exists():
                    # File exists but load() returned None - corrupted or unreadable
                    raise RuntimeError(
                        f"Cannot load settings from {self._path}. "
                        "File may be corrupted or encrypted with a different key. "
                        "Refusing to overwrite with defaults to prevent data loss."
                    )
                settings = PersistedSettings()
            updated = update_fn(settings)
            self.save(updated)
            return updated


class FileSecretsStore(SecretsStore):
    """File-based secrets storage.

    Stores secrets as encrypted JSON in a configurable directory.
    All secret values are encrypted using the provided cipher.

    Security features:
        - Files created with owner-only permissions (0o600)
        - Directory created with owner-only permissions (0o700)
        - Atomic writes to prevent corruption
        - File locking to prevent race conditions

    Note:
        On Windows, the 0o600 file permissions are not enforced by the
        filesystem. If storing secrets without encryption (cipher=None),
        they may be readable by other local users. Configure OH_SECRET_KEY
        to enable encryption for secure storage on all platforms.
    """

    def __init__(
        self,
        persistence_dir: Path | str,
        cipher: Cipher | None = None,
        filename: str = "secrets.json",
    ):
        # Use same validation as FileSettingsStore
        _validate_filename(filename)
        self.persistence_dir = Path(persistence_dir)
        self.cipher = cipher
        self.filename = filename
        self._path = self.persistence_dir / filename
        self._lock_path = self.persistence_dir / ".secrets.lock"

        # Warn about Windows security limitations when no encryption
        if sys.platform == "win32" and not cipher:
            logger.warning(
                "Storing secrets without encryption on Windows. "
                "File permissions are not enforced. Configure OH_SECRET_KEY "
                "for secure storage."
            )

    def load(self) -> Secrets | None:
        """Load secrets from file.

        If a cipher is provided, secrets are decrypted via Pydantic's
        validation context. The cipher is passed to model_validate which
        flows through to field validators using validate_secret().
        """
        if not self._path.exists():
            logger.debug(f"Secrets file not found: {self._path}")
            return None

        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # Pass cipher in context for automatic decryption of all secret fields
            context = {"cipher": self.cipher} if self.cipher else None
            return Secrets.model_validate(data, context=context)
        except (PermissionError, OSError) as e:
            # Critical filesystem errors should be re-raised
            logger.error(f"Cannot access secrets file: {e}")
            raise
        except json.JSONDecodeError as e:
            # Corrupted file - log and return None to allow recovery
            logger.error(f"Secrets file is corrupted: {e}")
            return None
        except Exception:
            # Validation or other errors - log and return None
            logger.error("Failed to load secrets", exc_info=True)
            return None

    def save(self, secrets: Secrets) -> None:
        """Save secrets to file atomically with secure permissions.

        If a cipher is provided, secrets are encrypted via Pydantic's
        serialization context. The cipher is passed to model_dump which
        flows through to field serializers using serialize_secret().

        Warning:
            This method does NOT acquire a file lock. For concurrent-safe
            updates, use :meth:`set_secret` or :meth:`delete_secret` which
            wrap save() with file locking. Direct calls to save() from
            multiple processes may cause lost updates.

        Warning:
            If no cipher is provided, secrets are stored in plaintext.
        """
        _ensure_secure_directory(self.persistence_dir)

        # Pass cipher in context for automatic encryption of all secret fields
        if self.cipher:
            context: dict[str, Any] = {"cipher": self.cipher}
        else:
            context = {"expose_secrets": "plaintext"}
            # Warn about plaintext secret storage (only if secrets exist)
            if secrets.custom_secrets:
                logger.warning(
                    "Saving secrets in PLAINTEXT (no cipher configured). "
                    "Configure OH_SECRET_KEY for production deployments."
                )

        data = secrets.model_dump(mode="json", context=context)

        _atomic_write_json(self._path, data)
        logger.debug(f"Secrets saved to {self._path}")

    def get_secret(self, name: str) -> str | None:
        """Get a single secret value by name.

        Uses file locking to prevent reading during concurrent writes.
        """
        with _file_lock(self._lock_path):
            secrets = self.load()
            if secrets is None:
                return None
            secret = secrets.custom_secrets.get(name)
            if secret is None or secret.secret is None:
                return None
            return secret.secret.get_secret_value()

    def set_secret(self, name: str, value: str, description: str | None = None) -> None:
        """Set a single secret with file locking to prevent race conditions.

        Raises:
            RuntimeError: If the secrets file exists but cannot be loaded
                (e.g., corrupted JSON, decryption failure). This prevents
                data loss from overwriting existing secrets with defaults.
        """
        with _file_lock(self._lock_path):
            secrets = self.load()
            if secrets is None:
                # File doesn't exist - safe to use defaults
                if self._path.exists():
                    # File exists but load() returned None - corrupted or unreadable
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "File may be corrupted or encrypted with a different key. "
                        "Refusing to overwrite with defaults to prevent data loss."
                    )
                secrets = Secrets()

            # Create new secrets dict with updated value
            new_secrets = dict(secrets.custom_secrets)
            new_secrets[name] = CustomSecret(
                name=name,
                secret=SecretStr(value),
                description=description,
            )

            # Save with frozen model copy
            self.save(Secrets(custom_secrets=new_secrets))

    def delete_secret(self, name: str) -> bool:
        """Delete a secret with file locking. Returns True if it existed.

        Raises:
            RuntimeError: If the secrets file exists but cannot be loaded
                (e.g., corrupted JSON, decryption failure). This prevents
                data loss from overwriting existing secrets with defaults.
        """
        with _file_lock(self._lock_path):
            secrets = self.load()
            if secrets is None:
                # File doesn't exist - nothing to delete
                if self._path.exists():
                    # File exists but load() returned None - corrupted or unreadable
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "File may be corrupted or encrypted with a different key. "
                        "Refusing to modify to prevent data loss."
                    )
                return False
            if name not in secrets.custom_secrets:
                return False

            new_secrets = {k: v for k, v in secrets.custom_secrets.items() if k != name}
            self.save(Secrets(custom_secrets=new_secrets))
            return True


# ── Global Store Access ──────────────────────────────────────────────────

_settings_store: FileSettingsStore | None = None
_secrets_store: FileSecretsStore | None = None
_store_lock = threading.Lock()


def _get_persistence_dir(config: Config | None = None) -> Path:
    """Get the persistence directory from config or default."""
    # Check environment variable first
    env_dir = os.environ.get("OH_PERSISTENCE_DIR")
    if env_dir:
        return Path(env_dir)

    # Use config's conversations_path parent if available
    if config is not None:
        return config.conversations_path.parent / ".openhands"

    return DEFAULT_PERSISTENCE_DIR


def _get_cipher(config: Config | None = None) -> Cipher | None:
    """Get cipher from config for encrypting secrets."""
    if config is not None:
        return config.cipher
    return None


def get_settings_store(config: Config | None = None) -> FileSettingsStore:
    """Get the global settings store instance (thread-safe).

    Note:
        The config parameter is only used on first initialization.
        Subsequent calls return the existing instance regardless of config.

    Warning:
        The cipher key (OH_SECRET_KEY) must NOT change during runtime.
        The store singleton caches the cipher from first initialization.
        If the cipher key changes:
        - New data may be encrypted with a stale key
        - Existing data may fail to decrypt
        - This could trigger data loss protection in update operations

        To use a new cipher key, restart the server process.
        For testing, use :func:`reset_stores` to clear the singletons.
    """
    global _settings_store
    if _settings_store is not None:
        return _settings_store

    with _store_lock:
        # Double-check after acquiring lock
        if _settings_store is None:
            _settings_store = FileSettingsStore(
                persistence_dir=_get_persistence_dir(config),
                cipher=_get_cipher(config),
            )
        return _settings_store


def get_secrets_store(config: Config | None = None) -> FileSecretsStore:
    """Get the global secrets store instance (thread-safe).

    Note:
        The config parameter is only used on first initialization.
        Subsequent calls return the existing instance regardless of config.

    Warning:
        The cipher key (OH_SECRET_KEY) must NOT change during runtime.
        The store singleton caches the cipher from first initialization.
        If the cipher key changes:
        - New data may be encrypted with a stale key
        - Existing data may fail to decrypt
        - This could trigger data loss protection in update operations

        To use a new cipher key, restart the server process.
        For testing, use :func:`reset_stores` to clear the singletons.
    """
    global _secrets_store
    if _secrets_store is not None:
        return _secrets_store

    with _store_lock:
        # Double-check after acquiring lock
        if _secrets_store is None:
            _secrets_store = FileSecretsStore(
                persistence_dir=_get_persistence_dir(config),
                cipher=_get_cipher(config),
            )
        return _secrets_store


def reset_stores() -> None:
    """Reset global store instances (for testing)."""
    global _settings_store, _secrets_store
    with _store_lock:
        _settings_store = None
        _secrets_store = None
