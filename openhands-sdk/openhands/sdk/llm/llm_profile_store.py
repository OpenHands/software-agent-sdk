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
    from openhands.sdk.settings.model import ACPAgentSettings
    from openhands.sdk.utils.cipher import Cipher


def _build_save_context(include_secrets: bool, cipher: Cipher | None) -> dict[str, Any]:
    """Pydantic serialization context for persisting a profile.

    When ``include_secrets`` is set, secrets are encrypted at rest if a
    ``cipher`` is available, else written in plaintext. When it is unset, an
    empty context masks secrets via the model serializers. Shared by
    :meth:`LLMProfileStore.save` and :meth:`LLMProfileStore.save_acp` so both
    paths handle secrets identically.
    """
    context: dict[str, Any] = {}
    if include_secrets:
        if cipher:
            context["cipher"] = cipher
            context["expose_secrets"] = "encrypted"
        else:
            context["expose_secrets"] = True
    return context


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
        context = _build_save_context(include_secrets, cipher)
        profile_json = json.dumps(llm.to_persisted(context=context), indent=2)
        self._persist_profile_json(name, profile_json, max_profiles=max_profiles)

    def save_acp(
        self,
        name: str,
        acp_settings: ACPAgentSettings,
        include_secrets: bool = False,
        *,
        cipher: Cipher | None = None,
        max_profiles: int | None = None,
    ) -> None:
        """Save an ACP AgentProfile to the profile directory.

        ACP counterpart to :meth:`save`. The on-disk JSON carries
        ``agent_kind: "acp"`` so :meth:`profile_kind` and :meth:`list_summaries`
        can tell it apart from a legacy bare-LLM (OpenHands) profile, and
        :meth:`load` rejects it (an ACP profile is not an :class:`LLM`).

        ``acp_env`` values and the bookkeeping ``llm`` secrets are encrypted at
        rest when ``cipher`` is provided, mirroring :meth:`save`.

        Args:
            name: Name of the profile to save.
            acp_settings: The ACP agent settings to persist.
            include_secrets: Whether to persist ``acp_env`` / ``llm`` secrets.
            cipher: Optional cipher for at-rest encryption of secrets.
            max_profiles: Optional cap on the number of profiles.

        Raises:
            ProfileLimitExceeded: If ``max_profiles`` would be exceeded.
            TimeoutError: If the lock cannot be acquired.
        """
        context = _build_save_context(include_secrets, cipher)
        profile_json = json.dumps(
            acp_settings.model_dump(mode="json", exclude_none=True, context=context),
            indent=2,
        )
        self._persist_profile_json(name, profile_json, max_profiles=max_profiles)

    def _persist_profile_json(
        self, name: str, profile_json: str, *, max_profiles: int | None
    ) -> None:
        """Atomically write ``profile_json`` to ``<name>.json`` under the lock.

        Shared by :meth:`save` (LLM profiles) and :meth:`save_acp` (ACP
        profiles) so the profile-limit check and atomic temp-file replace live
        in exactly one place.
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

    def profile_kind(self, name: str) -> str:
        """Return the AgentProfile kind for a saved profile.

        Reads the JSON directly (no model instantiation). A file without an
        ``agent_kind`` (legacy bare-LLM profile) or with ``"openhands"`` /
        ``"llm"`` reports ``"openhands"``; ``"acp"`` reports ``"acp"``. Callers
        use this to pick :meth:`load` vs :meth:`load_acp`.

        Raises:
            FileNotFoundError: If the profile does not exist.
            ValueError: If the profile file cannot be read/parsed.
        """
        profile_path = self._get_profile_path(name)
        with self._acquire_lock():
            if not profile_path.exists():
                raise FileNotFoundError(f"Profile `{name}` not found")
            try:
                data = json.loads(profile_path.read_text())
            except (OSError, json.JSONDecodeError) as e:
                raise ValueError(f"Failed to read profile `{name}`: {e}") from e
        kind = data.get("agent_kind") if isinstance(data, dict) else None
        return "acp" if kind == "acp" else "openhands"

    def load_acp(self, name: str, *, cipher: Cipher | None = None) -> ACPAgentSettings:
        """Load an ACP AgentProfile (:class:`ACPAgentSettings`) by name.

        ACP counterpart to :meth:`load`. Validates through
        :func:`validate_agent_settings` so ``acp_env`` / ``llm`` secrets are
        decrypted when ``cipher`` is provided.

        Raises:
            FileNotFoundError: If the profile does not exist.
            ValueError: If the file is corrupted or is not an ACP profile.
            TimeoutError: If the lock cannot be acquired.
        """
        from openhands.sdk.settings.model import (
            ACPAgentSettings,
            validate_agent_settings,
        )

        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if not profile_path.exists():
                existing = [p.name for p in self.base_dir.glob("*.json")]
                raise FileNotFoundError(
                    f"Profile `{name}` not found. "
                    f"Available profiles: {', '.join(existing) or 'none'}"
                )
            try:
                data = json.loads(profile_path.read_text())
                context: dict[str, Any] | None = {"cipher": cipher} if cipher else None
                settings = validate_agent_settings(data, context=context)
            except Exception as e:
                raise ValueError(f"Failed to load profile `{name}`: {e}") from e

        if not isinstance(settings, ACPAgentSettings):
            raise ValueError(
                f"Profile `{name}` is not an ACP profile "
                f"(agent_kind={getattr(settings, 'agent_kind', 'openhands')!r})"
            )
        logger.info(f"[Profile Store] Loaded ACP profile `{name}` from {profile_path}")
        return settings

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
                if data.get("agent_kind") == "acp":
                    summaries.append(_acp_summary(name, data))
                else:
                    summaries.append(_llm_summary(name, data))
        return summaries


def _is_set_secret(value: Any) -> bool:
    """True when a serialized secret carries a real (non-redacted) value."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value != REDACTED_SECRET_VALUE
    )


def _llm_summary(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Summary for a legacy bare-LLM (OpenHands) profile file."""
    return {
        "name": name,
        "kind": "openhands",
        "model": data.get("model"),
        "base_url": data.get("base_url"),
        "acp_server": None,
        "acp_model": None,
        "api_key_set": _is_set_secret(data.get("api_key")),
    }


def _acp_summary(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Summary for an ACP profile file.

    ``model`` mirrors ``acp_model`` so chip/label consumers that read ``model``
    still show something. ``api_key_set`` reflects whether the profile carries
    credentials in ``acp_env`` (the ACP subprocess's real auth surface).
    """
    acp_env = data.get("acp_env")
    api_key_set = isinstance(acp_env, dict) and any(
        _is_set_secret(v) for v in acp_env.values()
    )
    acp_model = data.get("acp_model")
    return {
        "name": name,
        "kind": "acp",
        "model": acp_model,
        "base_url": None,
        "acp_server": data.get("acp_server"),
        "acp_model": acp_model,
        "api_key_set": api_key_set,
    }
