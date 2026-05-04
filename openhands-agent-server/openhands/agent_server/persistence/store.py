"""File-based storage implementations for settings and secrets.

Following the same pattern as OpenHands app-server's FileSettingsStore
and FileSecretsStore for consistency.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import SecretStr

from openhands.agent_server.persistence.models import (
    CustomSecret,
    PersistedSettings,
    Secrets,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.cipher import Cipher


if TYPE_CHECKING:
    from openhands.agent_server.config import Config


logger = get_logger(__name__)

# File permission constants (owner read/write only)
_DIR_MODE = stat.S_IRWXU  # 0o700 - rwx------
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600 - rw-------


def _ensure_secure_directory(path: Path) -> None:
    """Ensure directory exists with secure permissions.

    Creates the directory with owner-only permissions (0o700) if it doesn't exist.
    If it already exists, ensures permissions are correct.
    """
    try:
        path.mkdir(parents=True, exist_ok=False, mode=_DIR_MODE)
    except FileExistsError:
        # Directory exists - ensure permissions are correct
        try:
            path.chmod(_DIR_MODE)
        except OSError:
            pass  # Best effort - may fail if not owner


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """Context manager for file-based locking (Unix fcntl).

    Provides exclusive lock to prevent race conditions during
    read-modify-write operations.
    """
    _ensure_secure_directory(lock_path.parent)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically with secure permissions.

    Uses write-to-temp-then-rename pattern to prevent corruption
    if interrupted. Creates temp file with owner-only permissions from
    the start to prevent race conditions where sensitive data could
    be read before chmod.
    """
    tmp_path = path.with_suffix(".tmp")
    # Create file with secure permissions from the start using os.open
    # O_WRONLY | O_CREAT | O_TRUNC mimics "w" mode
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        # Clean up on error
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
        self, update_fn: "Callable[[PersistedSettings], PersistedSettings]"
    ) -> PersistedSettings:
        """Atomically update settings with file locking.

        Args:
            update_fn: Function that takes current settings and returns updated settings.

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
        except Exception:
            logger.error("Failed to load settings", exc_info=True)
            return None

    def save(self, settings: PersistedSettings) -> None:
        """Save settings to file atomically with secure permissions.

        If a cipher is provided, secrets are encrypted via Pydantic's
        serialization context. The cipher is passed to model_dump which
        flows through to field serializers using serialize_secret().
        """
        _ensure_secure_directory(self.persistence_dir)

        # Pass cipher in context for automatic encryption of all secret fields
        # This flows through to field serializers using serialize_secret()
        context = {"cipher": self.cipher} if self.cipher else {"expose_secrets": True}
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
            update_fn: Function that takes current settings and returns updated settings.

        Returns:
            The updated settings after saving.
        """
        with _file_lock(self._lock_path):
            settings = self.load() or PersistedSettings()
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
    """

    def __init__(
        self,
        persistence_dir: Path | str,
        cipher: Cipher | None = None,
        filename: str = "secrets.json",
    ):
        self.persistence_dir = Path(persistence_dir)
        self.cipher = cipher
        self.filename = filename
        self._path = self.persistence_dir / filename
        self._lock_path = self.persistence_dir / ".secrets.lock"

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
        except Exception:
            logger.error("Failed to load secrets", exc_info=True)
            return None

    def save(self, secrets: Secrets) -> None:
        """Save secrets to file atomically with secure permissions.

        If a cipher is provided, secrets are encrypted via Pydantic's
        serialization context. The cipher is passed to model_dump which
        flows through to field serializers using serialize_secret().
        """
        _ensure_secure_directory(self.persistence_dir)

        # Pass cipher in context for automatic encryption of all secret fields
        context = {"cipher": self.cipher} if self.cipher else {"expose_secrets": True}
        data = secrets.model_dump(mode="json", context=context)

        _atomic_write_json(self._path, data)
        logger.debug(f"Secrets saved to {self._path}")

    def get_secret(self, name: str) -> str | None:
        """Get a single secret value by name."""
        secrets = self.load()
        if secrets is None:
            return None
        secret = secrets.custom_secrets.get(name)
        return secret.secret.get_secret_value() if secret else None

    def set_secret(self, name: str, value: str, description: str | None = None) -> None:
        """Set a single secret with file locking to prevent race conditions."""
        with _file_lock(self._lock_path):
            secrets = self.load() or Secrets()

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
        """Delete a secret with file locking. Returns True if it existed."""
        with _file_lock(self._lock_path):
            secrets = self.load()
            if secrets is None or name not in secrets.custom_secrets:
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

    Note: The config parameter is only used on first initialization.
    Subsequent calls return the existing instance regardless of config.
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

    Note: The config parameter is only used on first initialization.
    Subsequent calls return the existing instance regardless of config.
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
