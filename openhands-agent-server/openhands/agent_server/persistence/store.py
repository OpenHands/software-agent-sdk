"""File-based storage implementations for settings and secrets.

Following the same pattern as OpenHands app-server's FileSettingsStore
and FileSecretsStore for consistency.

File locking uses fcntl on Unix and msvcrt on Windows.
"""

from __future__ import annotations

import json
import os
import secrets as secrets_module
import stat
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

from pydantic import SecretStr

from openhands.agent_server.persistence.models import (
    CustomSecret,
    PersistedSettings,
    PersistedWorkspaces,
    Secrets,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnectionStore
from openhands.sdk.logger import get_logger
from openhands.sdk.profiles.agent_profile_store import AgentProfileStore
from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher
from openhands.sdk.utils.path import get_user_persistence_dir
from openhands.sdk.utils.pydantic_secrets import (
    STRICT_DECRYPT_CONTEXT_KEY,
    SecretDecryptionError,
)


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

    # Atomic rename - clean up temp file if replace() fails
    try:
        tmp_path.replace(path)  # Atomic on POSIX
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# Appended to the "different key" refusals of read-modify-write paths.
_KEY_HINT: Final[str] = (
    "restore the key it was saved under as OH_SECRET_KEY, or add it to "
    "OH_PREVIOUS_SECRET_KEYS"
)


def _has_stale_token(cipher: Cipher, data: Any) -> bool:
    """Whether ``data`` holds a Fernet token not under ``cipher``'s current key."""
    if isinstance(data, str):
        return data.startswith(FERNET_TOKEN_PREFIX) and not cipher.is_current(data)
    if isinstance(data, dict):
        return any(_has_stale_token(cipher, v) for v in data.values())
    if isinstance(data, list):
        return any(_has_stale_token(cipher, v) for v in data)
    return False


def _file_has_stale_token(path: Path, cipher: Cipher | None) -> bool:
    if cipher is None or not cipher.previous_keys or not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return _has_stale_token(cipher, data)


def _load_context(cipher: Cipher | None, strict: bool) -> dict[str, Any] | None:
    """Validation context for loading a store file with ``cipher``."""
    if cipher is None:
        return None
    return {"cipher": cipher, STRICT_DECRYPT_CONTEXT_KEY: strict}


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

    def load(self, *, strict: bool = False) -> PersistedSettings | None:
        """Load settings from file.

        If a cipher is provided, secrets are decrypted via Pydantic's
        validation context. The cipher is passed to model_validate which
        flows through to field validators using validate_secret().

        A secret the cipher cannot decrypt loads as ``None``. With
        ``strict=True`` (read-modify-write paths) the load fails instead,
        returning ``None``, so the caller refuses to overwrite the file.
        """
        if not self._path.exists():
            logger.debug(f"Settings file not found: {self._path}")
            return None

        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # Pass cipher in context for automatic decryption of all secret fields
            # This flows through to field validators using validate_secret()
            context = _load_context(self.cipher, strict)
            return PersistedSettings.from_persisted(data, context=context)
        except SecretDecryptionError as e:
            logger.error(f"Cannot load settings for update: {e}")
            return None
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
            if settings.has_any_secret:
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
            settings = self.load(strict=True)
            if settings is None:
                # File doesn't exist or is empty - safe to use defaults
                if self._path.exists():
                    # File exists but load() returned None - corrupted or unreadable
                    raise RuntimeError(
                        f"Cannot load settings from {self._path}. "
                        "File may be corrupted or encrypted with a different key "
                        f"({_KEY_HINT}). "
                        "Refusing to overwrite with defaults to prevent data loss."
                    )
                settings = PersistedSettings()
            updated = update_fn(settings)
            self.save(updated)
            return updated

    def reencrypt(self) -> bool:
        """Re-encrypt values saved under a previous key under the current key.

        Returns ``True`` when the file was rewritten. The file is left as is
        when nothing in it is stale, or when a value in it cannot be
        decrypted with any configured key.
        """
        with _file_lock(self._lock_path):
            if not _file_has_stale_token(self._path, self.cipher):
                return False
            settings = self.load(strict=True)
            if settings is None:
                return False
            self.save(settings)
            return True


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

    def load(self, *, strict: bool = False) -> Secrets | None:
        """Load secrets from file.

        If a cipher is provided, secrets are decrypted via Pydantic's
        validation context. The cipher is passed to model_validate which
        flows through to field validators using validate_secret().

        A secret the cipher cannot decrypt loads as ``None``. With
        ``strict=True`` (read-modify-write paths) the load fails instead,
        returning ``None``, so the caller refuses to overwrite the file.
        """
        if not self._path.exists():
            logger.debug(f"Secrets file not found: {self._path}")
            return None

        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # Pass cipher in context for automatic decryption of all secret fields
            context = _load_context(self.cipher, strict)
            return Secrets.model_validate(data, context=context)
        except SecretDecryptionError as e:
            logger.error(f"Cannot load secrets for update: {e}")
            return None
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
        """Save secrets while preserving credential generations."""
        with _file_lock(self._lock_path):
            current = self.load(strict=True)
            if current is None:
                if self._path.exists():
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "Refusing to overwrite existing data."
                    )
                current = Secrets()

            versions = self._load_versions()
            current_secrets = current.custom_secrets
            replacement_secrets = secrets.custom_secrets
            versioned_names = set(versions)

            for name in list(versions):
                replacement = replacement_secrets.get(name)
                if replacement is None or replacement.secret is None:
                    versions.pop(name, None)

            for name, replacement in replacement_secrets.items():
                if name not in versioned_names or replacement.secret is None:
                    continue
                previous = current_secrets.get(name)
                previous_value = (
                    previous.secret.get_secret_value()
                    if previous is not None and previous.secret is not None
                    else None
                )
                if previous_value != replacement.secret.get_secret_value():
                    versions[name] = secrets_module.token_urlsafe(24)

            self._save_with_versions(secrets, versions)

    def reencrypt(self) -> bool:
        """Re-encrypt values saved under a previous key under the current key.

        Returns ``True`` when the file was rewritten. The file is left as is
        when nothing in it is stale, or when a value in it cannot be
        decrypted with any configured key.
        """
        with _file_lock(self._lock_path):
            if not _file_has_stale_token(self._path, self.cipher):
                return False
            secrets = self.load(strict=True)
            if secrets is None:
                return False
            self._save_with_versions(secrets, self._load_versions())
            return True

    def _save_with_versions(
        self,
        secrets: Secrets,
        versions: dict[str, str],
    ) -> None:
        _ensure_secure_directory(self.persistence_dir)

        # Pass cipher in context for automatic encryption of all secret fields
        if self.cipher:
            context: dict[str, Any] = {"cipher": self.cipher}
        else:
            context = {"expose_secrets": "plaintext"}
            # Warn about plaintext secret storage (only if secrets exist)
            if secrets.has_any_secret:
                logger.warning(
                    "Saving secrets in PLAINTEXT (no cipher configured). "
                    "Configure OH_SECRET_KEY for production deployments."
                )

        data = secrets.model_dump(mode="json", context=context)
        if versions:
            data["_credential_versions"] = versions

        _atomic_write_json(self._path, data)
        logger.debug(f"Secrets saved to {self._path}")

    def _load_versions(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        versions = data.get("_credential_versions") if isinstance(data, dict) else None
        if not isinstance(versions, dict):
            return {}
        return {
            name: version
            for name, version in versions.items()
            if isinstance(name, str) and isinstance(version, str) and version
        }

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
            secrets = self.load(strict=True)
            if secrets is None:
                # File doesn't exist - safe to use defaults
                if self._path.exists():
                    # File exists but load() returned None - corrupted or unreadable
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "File may be corrupted or encrypted with a different key "
                        f"({_KEY_HINT}). "
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

            versions = self._load_versions()
            if name in versions:
                versions[name] = secrets_module.token_urlsafe(24)
            self._save_with_versions(Secrets(custom_secrets=new_secrets), versions)

    def delete_secret(self, name: str) -> bool:
        """Delete a secret with file locking. Returns True if it existed.

        Raises:
            RuntimeError: If the secrets file exists but cannot be loaded
                (e.g., corrupted JSON, decryption failure). This prevents
                data loss from overwriting existing secrets with defaults.
        """
        with _file_lock(self._lock_path):
            secrets = self.load(strict=True)
            if secrets is None:
                # File doesn't exist - nothing to delete
                if self._path.exists():
                    # File exists but load() returned None - corrupted or unreadable
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "File may be corrupted or encrypted with a different key "
                        f"({_KEY_HINT}). "
                        "Refusing to modify to prevent data loss."
                    )
                return False
            if name not in secrets.custom_secrets:
                return False

            new_secrets = {k: v for k, v in secrets.custom_secrets.items() if k != name}
            versions = self._load_versions()
            versions.pop(name, None)
            self._save_with_versions(Secrets(custom_secrets=new_secrets), versions)
            return True

    def load_versioned_secret(self, name: str) -> tuple[str, str]:
        with _file_lock(self._lock_path):
            secrets = self.load(strict=True)
            if secrets is None:
                if self._path.exists():
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "File may be corrupted or encrypted with a different key "
                        f"({_KEY_HINT}). "
                        "Refusing to modify to prevent data loss."
                    )
                raise KeyError(name)

            current = secrets.custom_secrets.get(name)
            if current is None or current.secret is None:
                raise KeyError(name)
            versions = self._load_versions()
            version = versions.get(name)
            if version is None:
                version = secrets_module.token_urlsafe(24)
                versions[name] = version
                self._save_with_versions(secrets, versions)
            return current.secret.get_secret_value(), version

    def replace_versioned_secret(
        self,
        name: str,
        expected_version: str,
        value: str,
    ) -> str:
        with _file_lock(self._lock_path):
            secrets = self.load(strict=True)
            if secrets is None:
                if self._path.exists():
                    raise RuntimeError(
                        f"Cannot load secrets from {self._path}. "
                        "File may be corrupted or encrypted with a different key "
                        f"({_KEY_HINT}). "
                        "Refusing to modify to prevent data loss."
                    )
                raise KeyError(name)

            current = secrets.custom_secrets.get(name)
            if current is None or current.secret is None:
                raise KeyError(name)
            versions = self._load_versions()
            if versions.get(name) != expected_version:
                raise ValueError("credential_version_conflict")

            new_secrets = dict(secrets.custom_secrets)
            new_secrets[name] = current.model_copy(update={"secret": SecretStr(value)})
            successor = secrets_module.token_urlsafe(24)
            versions[name] = successor
            self._save_with_versions(Secrets(custom_secrets=new_secrets), versions)
            return successor


class WorkspacesStore(ABC):
    """Abstract base class for workspaces storage."""

    @abstractmethod
    def load(self) -> PersistedWorkspaces | None:
        """Load workspaces from storage."""

    @abstractmethod
    def save(self, workspaces: PersistedWorkspaces) -> None:
        """Save workspaces to storage."""

    @abstractmethod
    def update(
        self,
        update_fn: Callable[[PersistedWorkspaces], PersistedWorkspaces],
    ) -> PersistedWorkspaces:
        """Atomically update workspaces with file locking.

        ``update_fn`` returns its argument unchanged to signal a no-op, in
        which case nothing is written.
        """


class FileWorkspacesStore(WorkspacesStore):
    """File-based storage for the user's saved workspaces / workspace parents.

    Persists a single JSON document at ``<persistence_dir>/workspaces.json``
    using the same atomic-write + file-lock primitives as ``FileSettingsStore``.
    Workspace paths are not secret, so no cipher is used.
    """

    def __init__(
        self,
        persistence_dir: Path | str,
        filename: str = "workspaces.json",
    ):
        _validate_filename(filename)
        self.persistence_dir = Path(persistence_dir)
        self.filename = filename
        self._path = self.persistence_dir / filename
        self._lock_path = self.persistence_dir / ".workspaces.lock"

    def load(self) -> PersistedWorkspaces | None:
        if not self._path.exists():
            return None

        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return PersistedWorkspaces.from_persisted(data)
        except (PermissionError, OSError) as e:
            logger.error(f"Cannot access workspaces file: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Workspaces file is corrupted: {e}")
            return None
        except Exception:
            logger.error("Failed to load workspaces", exc_info=True)
            return None

    def save(self, workspaces: PersistedWorkspaces) -> None:
        _ensure_secure_directory(self.persistence_dir)
        # ``exclude_none=True`` keeps the on-disk shape aligned with the wire
        # contract: unset ``parentPath`` is absent rather than ``null``, which
        # matches the GUI's ``LocalWorkspace.parentPath?: string`` type.
        data = workspaces.model_dump(mode="json", by_alias=True, exclude_none=True)
        _atomic_write_json(self._path, data)
        logger.debug(f"Workspaces saved to {self._path}")

    def update(
        self,
        update_fn: Callable[[PersistedWorkspaces], PersistedWorkspaces],
    ) -> PersistedWorkspaces:
        with _file_lock(self._lock_path):
            workspaces = self.load()
            if workspaces is None:
                if self._path.exists():
                    raise RuntimeError(
                        f"Cannot load workspaces from {self._path}. "
                        "File may be corrupted. "
                        "Refusing to overwrite with defaults to prevent data loss."
                    )
                workspaces = PersistedWorkspaces()
            updated = update_fn(workspaces)
            # ``update_fn`` returns its input unchanged when there is nothing
            # to change (a re-add, a delete that matched nothing); skip the
            # write so a no-op neither creates nor rewrites the file.
            if updated is not workspaces:
                self.save(updated)
            return updated


# ── Per-server stores ────────────────────────────────────────────────────


def _get_persistence_dir(config: Config) -> Path:
    """Get the workspace-relative persistence directory for ``config``."""
    # Absent OH_PERSISTENCE_DIR, fall back to config's conversations_path parent.
    return get_user_persistence_dir(config.conversations_path.parent / ".openhands")


def _get_profile_persistence_dir() -> Path:
    """Get the base dir for the settings, secrets and profile stores.

    These can hold credentials (LLM API keys), so absent
    ``OH_PERSISTENCE_DIR`` they fall back to the user's ``~/.openhands``
    rather than the workspace-relative agent-server default, keeping bare
    local profile secrets in the expected user config directory.
    """
    return get_user_persistence_dir()


@dataclass(frozen=True)
class ServerStores:
    """The persistent stores of one server (re)initialisation.

    A server builds one instance per :class:`Config` it runs with (at start-up
    and again on ``POST /api/init``) and reaches every store through it, so all
    of them agree on the cipher and the directories resolved when it was built.
    There is no process-wide store: code that needs one is handed the instance
    (or the store) it should use.

    Building the instance touches no file. Each store is created on first use
    and then reused; several of their constructors create their directory.

    Warning:
        The cipher key is fixed for the lifetime of the instance. To use a new
        key, build a new instance from the new config.
    """

    cipher: Cipher | None
    """Encrypts the settings and secrets stores at rest."""
    profile_dir: Path
    """Base of the settings, secrets, provider-connection and profile stores:
    ``OH_PERSISTENCE_DIR`` when set, else ``~/.openhands``."""
    workspace_dir: Path
    """Base of the workspaces store: ``OH_PERSISTENCE_DIR`` when set, else
    ``.openhands`` next to the config's conversations directory."""
    _lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )
    _built: dict[str, object] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @classmethod
    def from_config(cls, config: Config) -> ServerStores:
        """Resolve the cipher and directories ``config`` selects."""
        return cls(
            cipher=config.cipher,
            profile_dir=_get_profile_persistence_dir(),
            workspace_dir=_get_persistence_dir(config),
        )

    def _once[StoreT](self, name: str, build: Callable[[], StoreT]) -> StoreT:
        with self._lock:
            if name not in self._built:
                self._built[name] = build()
            return cast(StoreT, self._built[name])

    @property
    def settings(self) -> FileSettingsStore:
        return self._once(
            "settings",
            lambda: FileSettingsStore(
                persistence_dir=self.profile_dir, cipher=self.cipher
            ),
        )

    @property
    def secrets(self) -> FileSecretsStore:
        return self._once(
            "secrets",
            lambda: FileSecretsStore(
                persistence_dir=self.profile_dir, cipher=self.cipher
            ),
        )

    @property
    def provider_connections(self) -> ProviderConnectionStore:
        """Stored at ``<profile_dir>/provider-connections``.

        The store itself is cipher-agnostic; callers pass the cipher per call
        so keys are encrypted at rest.
        """
        return self._once(
            "provider_connections",
            lambda: ProviderConnectionStore(
                base_dir=self.profile_dir / "provider-connections"
            ),
        )

    @property
    def workspaces(self) -> FileWorkspacesStore:
        return self._once(
            "workspaces",
            lambda: FileWorkspacesStore(persistence_dir=self.workspace_dir),
        )

    @property
    def llm_profiles(self) -> LLMProfileStore:
        """Stored at ``<profile_dir>/profiles``, never workspace-relative."""
        return self._once(
            "llm_profiles",
            lambda: LLMProfileStore(
                base_dir=self.profile_dir / "profiles",
                provider_store=self.provider_connections,
            ),
        )

    @property
    def agent_profiles(self) -> AgentProfileStore:
        """Stored at ``<profile_dir>/agent-profiles``."""
        return self._once(
            "agent_profiles",
            lambda: AgentProfileStore(base_dir=self.profile_dir / "agent-profiles"),
        )


def reencrypt_stale_stores(stores: ServerStores) -> None:
    """Re-encrypt the settings and secrets stores under the current key.

    Runs at startup when ``previous_secret_keys`` is configured, so a rotated
    key does not wait for the next write. Failures are logged, never raised:
    the stores stay readable through the previous keys either way.
    """
    cipher = stores.cipher
    if cipher is None or not cipher.previous_keys:
        return
    for name, store in (
        ("settings", stores.settings),
        ("secrets", stores.secrets),
    ):
        try:
            if store.reencrypt():
                logger.info(f"Re-encrypted the {name} store under the current key")
        except Exception:
            logger.error(f"Could not re-encrypt the {name} store", exc_info=True)
