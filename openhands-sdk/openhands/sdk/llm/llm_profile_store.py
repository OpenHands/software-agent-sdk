# Required: ``LLMProfileStore.list()`` shadows the builtin in the class body,
# so annotations like ``list[dict[str, Any]]`` would fail without deferral.
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from filelock import FileLock, Timeout

from openhands.sdk.llm.utils.openhands_provider import (
    canonicalize_openhands_llm_payload,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.llm.provider_connection_store import ProviderConnectionStore
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


def _api_key_present(llm: LLM) -> bool:
    """True when ``llm`` carries a non-empty, non-redacted API key."""
    from pydantic import SecretStr

    api_key = llm.api_key
    if api_key is None:
        return False
    value = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
    return bool(value.strip()) and value != REDACTED_SECRET_VALUE


@runtime_checkable
class LLMProfileLoader(Protocol):
    """Minimal load-only contract consumed by ``resolve_agent_profile``.

    The resolver reads an LLM profile by name via ``load`` only, so an alternate
    backend — e.g. a cloud adapter over the ``org.llm_profiles`` column — need
    implement just this, not the full file-backed :class:`LLMProfileStore`.
    """

    def load(self, name: str, *, cipher: Cipher | None = ...) -> LLM: ...


@runtime_checkable
class LLMProfileMutator(Protocol):
    """Delete/rename contract used by the cross-store FK helpers.

    ``profile_refs.delete_llm_profile`` / ``rename_llm_profile`` touch the LLM
    store only through ``delete`` / ``rename``, so a cloud adapter over
    ``org.llm_profiles`` can drive the same guarded FK lifecycle without being a
    full :class:`LLMProfileStore`.
    """

    def delete(self, name: str) -> None: ...

    def rename(self, old_name: str, new_name: str) -> None: ...


class LLMProfileStore:
    """Standalone utility for persisting LLM configurations."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        provider_store: ProviderConnectionStore | None = None,
    ) -> None:
        """Initialize the profile store.

        Args:
            base_dir: Path to the directory where the profiles are stored.
                If `None` is provided, the default directory is used, i.e.,
                `~/.openhands/profiles`.
            provider_store: Store of shared provider connections used to
                resolve a profile's ``provider_connection_id`` at load time.
                When `None` (the default), a :class:`ProviderConnectionStore`
                is created at its own default directory so that every
                ``LLMProfileStore`` — including bare standalone SDK instances —
                can resolve linked profiles. Pass an explicit store to use a
                non-default directory (e.g. the agent-server's config-scoped
                directory) or a pre-constructed test double.
        """
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_PROFILE_DIR
        # ensure directory existence
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file_lock = FileLock(self.base_dir / ".profiles.lock")
        if provider_store is None:
            from openhands.sdk.llm.provider_connection_store import (
                ProviderConnectionStore,
            )

            self._provider_store: ProviderConnectionStore | None = (
                ProviderConnectionStore()
            )
        else:
            self._provider_store = provider_store

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

            # Rule 5c: a profile that references a provider connection owns no
            # inline credentials — the connection is the single source of truth,
            # so clear any api_key / base_url before persisting to avoid a stale
            # copy that could later disagree with the connection.
            if llm.provider_connection_id:
                llm = llm.model_copy(update={"api_key": None, "base_url": None})

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

    def load(
        self,
        name: str,
        *,
        cipher: Cipher | None = None,
        resolve_provider: bool = True,
    ) -> LLM:
        """Load an LLM instance from the given profile name.

        Args:
            name: Name of the profile to load.
            cipher: Optional cipher for decrypting secrets stored at rest.
                When provided, encrypted secrets are decrypted during load.
            resolve_provider: When True (default) and the profile references a
                provider connection, its shared ``api_key`` / ``base_url`` are
                applied to the returned LLM (read-at-use). Set False to inspect
                the profile as stored without touching the provider store — used
                by display paths so a dangling reference never fails a read.

        Returns:
            An LLM instance constructed from the profile configuration, with
            provider-connection credentials applied when ``resolve_provider``.

        Raises:
            FileNotFoundError: If the profile name does not exist.
            ValueError: If the profile file is corrupted or invalid.
            ProviderConnectionNotFound: If the profile references a provider
                connection that no longer exists and carries no inline key.
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

        if resolve_provider:
            llm_instance = self._resolve_provider_connection(
                name, llm_instance, cipher=cipher
            )
        return llm_instance

    def _resolve_provider_connection(
        self, profile_name: str, llm: LLM, *, cipher: Cipher | None
    ) -> LLM:
        """Apply a referenced provider connection's credentials to ``llm``.

        Rules:
        - no ``provider_connection_id`` -> unchanged (byte-identical old path).
        - no provider store configured -> unchanged (inert field).
        - connection found -> its ``api_key`` / ``base_url`` win (``base_url``
          is applied as-is, including ``None``).
        - connection missing -> raise :class:`ProviderConnectionNotFound`.

        The inline-key fallback below is not a recovery path for the usual
        "linked profile, connection later deleted" case: :meth:`save` strips
        inline creds from any linked profile, so on disk there is no inline key
        to fall back to and this raises. It only applies to an LLM whose
        ``provider_connection_id`` was set without going through :meth:`save`
        (e.g. constructed in memory).
        """
        connection_id = llm.provider_connection_id
        if not connection_id or self._provider_store is None:
            return llm

        from openhands.sdk.llm.provider_connection_store import (
            ProviderConnectionNotFound,
        )

        connection = self._provider_store.get(connection_id, cipher=cipher)
        if connection is None:
            if _api_key_present(llm):
                logger.warning(
                    "[Profile Store] Profile %r references missing provider "
                    "connection %r; falling back to the profile's inline key.",
                    profile_name,
                    connection_id,
                )
                return llm
            raise ProviderConnectionNotFound(
                f"Profile {profile_name!r} references provider connection "
                f"{connection_id!r}, which does not exist. Update the profile or "
                "recreate the connection."
            )

        updates: dict[str, Any] = {"base_url": connection.base_url}
        api_key = connection.api_key_value()
        if api_key is not None:
            from pydantic import SecretStr

            updates["api_key"] = SecretStr(api_key)
        return llm.model_copy(update=updates)

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

        A linked provider connection's key presence is read without a cipher:
        this method only reports whether a key is set, never its plaintext, so
        an encrypted-at-rest key still reads as present without decryption.
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
                data = canonicalize_openhands_llm_payload(data)
                api_key = data.get("api_key")
                api_key_set = (
                    isinstance(api_key, str)
                    and bool(api_key.strip())
                    and api_key != REDACTED_SECRET_VALUE
                )
                connection_id = data.get("provider_connection_id")
                # A profile linked to a provider connection carries no inline
                # key (cleared on save), so its effective key presence lives on
                # the connection.
                provider_connection_broken = False
                if isinstance(connection_id, str) and self._provider_store is not None:
                    connection = self._provider_store.get(connection_id)
                    if connection is None:
                        provider_connection_broken = True
                    elif not api_key_set:
                        api_key_set = connection.api_key_value() is not None
                summaries.append(
                    {
                        "name": name,
                        "model": data.get("model"),
                        "base_url": data.get("base_url"),
                        "provider_connection_id": connection_id,
                        "provider_connection_broken": provider_connection_broken,
                        "api_key_set": api_key_set,
                    }
                )
        return summaries
