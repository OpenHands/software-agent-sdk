# Required: ``LLMProfileStore.list()`` shadows the builtin in the class body,
# so annotations like ``list[dict[str, Any]]`` would fail without deferral.
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from filelock import FileLock, Timeout

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.utils.cipher import Cipher

_DEFAULT_PROFILE_DIR: Final[Path] = Path.home() / ".openhands" / "profiles"
_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0

# Profile names: 1-64 chars, must start with alphanumeric, then alphanumerics
# or '.', '_', '-'. Blocks empty names, path separators, leading dots
# (hidden files / path traversal), and shell-special characters.
PROFILE_NAME_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
PROFILE_NAME_REGEX: Final[re.Pattern[str]] = re.compile(PROFILE_NAME_PATTERN)

logger = get_logger(__name__)


class ProfileLimitExceeded(Exception):
    """Raised when saving would exceed the configured profile limit."""


class LLMProfileStore:
    """Standalone utility for persisting LLM configurations."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """Initialize the profile store.

        Args:
            base_dir: Path to the directory where the profiles are stored.
                If `None` is provided, the default directory is used, i.e.,
                `~/.openhands/profiles`.
        """
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_PROFILE_DIR
        # ensure directory existence
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file_lock = FileLock(self.base_dir / ".profiles.lock")

    @contextmanager
    def _acquire_lock(self, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
        """Acquire file lock for safe concurrent access.

        Args:
            timeout: Maximum time to wait for lock acquisition in seconds.

        Raises:
            TimeoutError: If the lock cannot be acquired within the timeout.
        """
        try:
            with self._file_lock.acquire(timeout=timeout):
                yield
        except Timeout:
            logger.error(f"[Profile Store] Failed to acquire lock within {timeout}s")
            raise TimeoutError(
                f"Profile store lock acquisition timed out after {timeout}s"
            )

    def list(self) -> list[str]:
        """Returns a list of all profiles stored.

        Returns:
            List of profile filenames (e.g., ["default.json", "gpt4.json"]).
        """
        with self._acquire_lock():
            return [p.name for p in self.base_dir.glob("*.json")]

    def _get_profile_path(self, name: str) -> Path:
        """Get the full path for a profile name.

        Args:
            name: Profile name (must match ``PROFILE_NAME_PATTERN``).

        Raises:
            ValueError: If name does not match the allowed pattern.
        """
        clean_name = name.removesuffix(".json")
        if not PROFILE_NAME_REGEX.match(clean_name):
            raise ValueError(
                f"Invalid profile name: {name!r}. "
                "Profile names must be 1-64 characters, start with a letter "
                "or digit, and contain only letters, digits, '.', '_', or '-'."
            )
        return self.base_dir / f"{clean_name}.json"

    def save(
        self,
        name: str,
        llm: LLM,
        include_secrets: bool = False,
        *,
        cipher: Cipher | None = None,
        max_profiles: int | None = None,
    ) -> None:
        """Save a profile to the profile directory.

        Overwrites an existing profile of the same name. When ``max_profiles``
        is set, raises ``ProfileLimitExceeded`` if creating a *new* profile
        would exceed the limit. The check happens under the same lock as the
        save, so it is race-free against other ``save`` calls in this process.

        Args:
            name: Name of the profile to save.
            llm: LLM instance to save
            include_secrets: Whether to include the profile secrets. Defaults to False.
            cipher: Optional cipher for at-rest encryption of secrets.
                When provided, secrets are encrypted before writing to disk.
            max_profiles: Optional cap on the number of profiles.

        Raises:
            ProfileLimitExceeded: If ``max_profiles`` would be exceeded.
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if max_profiles is not None and not profile_path.exists():
                # Only count files visible via list_summaries (valid names),
                # so stray invalid files don't consume slots.
                count = sum(
                    1
                    for p in self.base_dir.glob("*.json")
                    if PROFILE_NAME_REGEX.match(p.stem)
                )
                if count >= max_profiles:
                    raise ProfileLimitExceeded(
                        f"Profile limit reached ({max_profiles})."
                    )

            if profile_path.exists():
                logger.info(
                    f"[Profile Store] Profile `{name}` already exists. Overwriting."
                )

            context: dict[str, Any] = {}
            if include_secrets:
                if cipher:
                    context["cipher"] = cipher
                    context["expose_secrets"] = "encrypted"
                else:
                    context["expose_secrets"] = True

            profile_json = json.dumps(llm.to_persisted(context=context), indent=2)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.base_dir, suffix=".tmp", delete=False
            ) as tmp:
                tmp.write(profile_json)
                tmp_path = Path(tmp.name)

            try:
                Path.replace(tmp_path, profile_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            logger.info(f"[Profile Store] Saved profile `{name}` at {profile_path}")

    def load(self, name: str, *, cipher: Cipher | None = None) -> LLM:
        """Load an LLM instance from the given profile name.

        Args:
            name: Name of the profile to load.
            cipher: Optional cipher for decrypting secrets stored at rest.
                When provided, encrypted secrets are decrypted during load.

        Returns:
            An LLM instance constructed from the profile configuration.

        Raises:
            FileNotFoundError: If the profile name does not exist.
            ValueError: If the profile file is corrupted or invalid.
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if not profile_path.exists():
                existing = [p.name for p in self.base_dir.glob("*.json")]
                raise FileNotFoundError(
                    f"Profile `{name}` not found. "
                    f"Available profiles: {', '.join(existing) or 'none'}"
                )

            try:
                from openhands.sdk.llm.llm import LLM

                context: dict[str, Any] | None = {"cipher": cipher} if cipher else None

                llm_instance = LLM.load_from_json(str(profile_path), context=context)
            except Exception as e:
                # Re-raise as ValueError for clearer error handling
                raise ValueError(f"Failed to load profile `{name}`: {e}") from e

            logger.info(f"[Profile Store] Loaded profile `{name}` from {profile_path}")
            return llm_instance

    def delete(self, name: str) -> None:
        """Delete an existing profile.

        If the profile is not present in the profile directory, it does nothing.

        Args:
            name: Name of the profile to delete.

        Raises:
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if not profile_path.exists():
                logger.info(f"[Profile Store] Profile `{name}` not found. Skipping.")
                return

            profile_path.unlink()
            logger.info(f"[Profile Store] Deleted profile `{name}`")

    def rename(self, old_name: str, new_name: str) -> None:
        """Atomically rename a profile.

        Raises FileNotFoundError if ``old_name`` is missing, FileExistsError
        if ``new_name`` is taken. When the names resolve to the same path,
        the call is a no-op but still verifies the profile exists.
        """
        old_path = self._get_profile_path(old_name)
        new_path = self._get_profile_path(new_name)

        with self._acquire_lock():
            if not old_path.exists():
                raise FileNotFoundError(f"Profile `{old_name}` not found")
            if old_path == new_path:
                return
            if new_path.exists():
                raise FileExistsError(f"Profile `{new_name}` already exists")
            old_path.rename(new_path)
            logger.info(f"[Profile Store] Renamed profile `{old_name}` to `{new_name}`")

    def list_summaries(self) -> list[dict[str, Any]]:
        """List profile metadata without instantiating LLM objects.

        Reads JSON directly to avoid ``LLM._set_env_side_effects`` mutating
        ``os.environ``. Files with invalid names, corrupted JSON, or non-dict
        top-level values are skipped with a warning.
        """
        summaries: list[dict[str, Any]] = []
        with self._acquire_lock():
            for path in sorted(self.base_dir.glob("*.json")):
                name = path.stem
                if not PROFILE_NAME_REGEX.match(name):
                    logger.warning(
                        f"[Profile Store] Skipping profile with invalid name {name!r}"
                    )
                    continue
                try:
                    data = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(
                        f"[Profile Store] Skipping corrupted profile {name!r}: {e}"
                    )
                    continue
                if not isinstance(data, dict):
                    logger.warning(
                        f"[Profile Store] Skipping non-dict profile {name!r}"
                    )
                    continue
                api_key = data.get("api_key")
                api_key_set = (
                    isinstance(api_key, str)
                    and bool(api_key.strip())
                    and api_key != REDACTED_SECRET_VALUE
                )
                summaries.append(
                    {
                        "name": name,
                        "model": data.get("model"),
                        "base_url": data.get("base_url"),
                        "api_key_set": api_key_set,
                    }
                )
        return summaries
