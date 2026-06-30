# Required: ``AgentProfileStore.list()`` shadows the builtin in the class body,
# so annotations like ``list[dict[str, Any]]`` would fail without deferral.
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable
from uuid import UUID, uuid4

from filelock import FileLock, Timeout

from openhands.sdk.logger import get_logger
from openhands.sdk.profiles.agent_profile import validate_agent_profile


if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from openhands.sdk.profiles.agent_profile import (
        ACPAgentProfile,
        OpenHandsAgentProfile,
    )
    from openhands.sdk.utils.cipher import Cipher

_DEFAULT_PROFILE_DIR: Final[Path] = Path.home() / ".openhands" / "agent-profiles"
_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0

# Profile names: 1-64 chars, must start with alphanumeric, then alphanumerics
# or '.', '_', '-'. Blocks empty names, path separators, leading dots
# (hidden files / path traversal), and shell-special characters. Identical to
# ``LLMProfileStore.PROFILE_NAME_PATTERN`` so the two stores share a naming
# contract (an ``llm_profile_ref`` is an LLM-store key).
PROFILE_NAME_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
PROFILE_NAME_REGEX: Final[re.Pattern[str]] = re.compile(PROFILE_NAME_PATTERN)

logger = get_logger(__name__)


class ProfileLimitExceeded(Exception):
    """Raised when saving would exceed the configured profile limit."""


class AgentProfileStore:
    """Standalone utility for persisting ``AgentProfile`` launch specs.

    Mirrors :class:`~openhands.sdk.llm.llm_profile_store.LLMProfileStore`: one
    JSON file per profile under ``~/.openhands/agent-profiles``, the filename is
    the (renameable) profile ``name``, and the stable ``id`` (uuid) lives inside
    the file. The profile is secret-free at rest except for
    ``skills[].mcp_tools`` env/headers, which are masked by default and
    encrypted when a ``cipher`` is supplied.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """Initialize the profile store.

        Args:
            base_dir: Directory where profiles are stored. ``None`` uses the
                default ``~/.openhands/agent-profiles``.
        """
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_PROFILE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file_lock = FileLock(self.base_dir / ".agent-profiles.lock")

    @contextmanager
    def _acquire_lock(self, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
        """Acquire the store file lock for safe concurrent access.

        ``filelock.FileLock`` is re-entrant within a thread, so FK helpers may
        nest this around the per-method calls without deadlocking.
        """
        try:
            with self._file_lock.acquire(timeout=timeout):
                yield
        except Timeout:
            logger.error(f"[AgentProfile Store] Failed to acquire lock in {timeout}s")
            raise TimeoutError(
                f"Agent profile store lock acquisition timed out after {timeout}s"
            )

    def lock(
        self, timeout: float = _LOCK_TIMEOUT_SECONDS
    ) -> AbstractContextManager[None]:
        """Public, re-entrant store lock for the FK helpers (``profile_refs``).

        Part of :class:`AgentProfileStoreProtocol` so a non-file store (e.g. a
        DB-backed cloud store) can supply its own re-entrant transaction guard
        under the same name. Delegates to :meth:`_acquire_lock`.
        """
        return self._acquire_lock(timeout)

    def list(self) -> list[str]:
        """Return the filenames of all stored profiles (e.g. ``["a.json"]``)."""
        with self._acquire_lock():
            return [p.name for p in self.base_dir.glob("*.json")]

    def _get_profile_path(self, name: str) -> Path:
        """Resolve a profile name to its file path, validating the name.

        Raises:
            ValueError: If ``name`` does not match ``PROFILE_NAME_PATTERN``.
        """
        clean_name = name.removesuffix(".json")
        if not PROFILE_NAME_REGEX.match(clean_name):
            raise ValueError(
                f"Invalid profile name: {name!r}. "
                "Profile names must be 1-64 characters, start with a letter "
                "or digit, and contain only letters, digits, '.', '_', or '-'."
            )
        return self.base_dir / f"{clean_name}.json"

    def _atomic_write(self, path: Path, text: str) -> None:
        """Write ``text`` to ``path`` via a temp file + atomic ``Path.replace``.

        Callers must hold :meth:`_acquire_lock`. Shared with ``profile_refs`` so
        the cascade rewrite reuses the same crash-safe write.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self.base_dir, suffix=".tmp", delete=False
        ) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        try:
            Path.replace(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def save(
        self,
        profile: OpenHandsAgentProfile | ACPAgentProfile,
        *,
        cipher: Cipher | None = None,
        max_profiles: int | None = None,
    ) -> None:
        """Save a profile under its own ``name``, overwriting any namesake.

        With no ``cipher`` the profile is secret-free at rest:
        ``skills[].mcp_tools`` env/headers are redacted. With a ``cipher`` those
        values are encrypted (recoverable) rather than redacted; every other
        field is a reference and dumps in the clear regardless. When
        ``max_profiles`` is set, creating a *new* profile beyond the cap raises
        ``ProfileLimitExceeded`` under the same lock as the write.

        Raises:
            ValueError: If ``profile.name`` is not a valid profile name.
            ProfileLimitExceeded: If ``max_profiles`` would be exceeded.
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(profile.name)

        # Cipher present => encrypt mcp_tools secrets; absent => redact them.
        context: dict[str, Any] = (
            {"cipher": cipher, "expose_secrets": "encrypted"} if cipher else {}
        )
        payload = profile.model_dump(mode="json", context=context)

        with self._acquire_lock():
            if max_profiles is not None and not profile_path.exists():
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
                    f"[AgentProfile Store] Overwriting profile `{profile.name}`."
                )

            self._atomic_write(profile_path, json.dumps(payload, indent=2))
            logger.info(
                f"[AgentProfile Store] Saved profile `{profile.name}` at {profile_path}"
            )

    def load(
        self,
        name: str,
        *,
        cipher: Cipher | None = None,
    ) -> OpenHandsAgentProfile | ACPAgentProfile:
        """Load and validate the profile stored under ``name``.

        A ``cipher`` is threaded through the validation context for parity with
        ``save``. Note: ``Skill.mcp_tools`` has a masking *serializer* but no
        symmetric *validator*, so encrypted env/headers come back as ciphertext
        — decryption is deferred to the resolver (#3717), which holds the
        cipher. Reference fields (``llm_profile_ref`` etc.) carry no secret and
        load unchanged.

        Raises:
            FileNotFoundError: If ``name`` does not exist.
            ValueError: If the file is corrupted or fails validation.
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
                data = json.loads(profile_path.read_text())
                context = {"cipher": cipher} if cipher else None
                profile = validate_agent_profile(data, context=context)
            except Exception as e:
                raise ValueError(f"Failed to load profile `{name}`: {e}") from e

            logger.info(
                f"[AgentProfile Store] Loaded profile `{name}` from {profile_path}"
            )
            return profile

    def delete(self, name: str) -> None:
        """Delete a profile, or no-op if it is absent.

        Raises:
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if not profile_path.exists():
                logger.info(
                    f"[AgentProfile Store] Profile `{name}` not found. Skipping."
                )
                return
            profile_path.unlink()
            logger.info(f"[AgentProfile Store] Deleted profile `{name}`")

    def rename(self, old_name: str, new_name: str) -> None:
        """Atomically rename a profile, keeping the in-file ``name`` in sync.

        Unlike a bare file move this also rewrites the persisted ``name`` field
        (a surgical raw-JSON edit, so encrypted ``mcp_tools`` are untouched and
        no cipher is needed). The stable ``id`` is preserved.

        Raises:
            FileNotFoundError: If ``old_name`` is missing.
            FileExistsError: If ``new_name`` is taken.
            ValueError: If either name is invalid.
        """
        old_path = self._get_profile_path(old_name)
        new_path = self._get_profile_path(new_name)
        new_stem = new_path.stem

        with self._acquire_lock():
            if not old_path.exists():
                raise FileNotFoundError(f"Profile `{old_name}` not found")
            if old_path == new_path:
                return
            if new_path.exists():
                raise FileExistsError(f"Profile `{new_name}` already exists")

            data = json.loads(old_path.read_text())
            if isinstance(data, dict):
                data["name"] = new_stem
            self._atomic_write(new_path, json.dumps(data, indent=2))
            old_path.unlink()
            logger.info(
                f"[AgentProfile Store] Renamed profile `{old_name}` to `{new_name}`"
            )

    def set_llm_profile_ref(self, name: str, new_ref: str) -> None:
        """Surgically repoint one OpenHands profile's ``llm_profile_ref``.

        The single-profile write primitive behind ``profile_refs.cascade_rename``
        — callers hold :meth:`lock`. A raw-JSON edit (only the ref field
        changes), so encrypted ``skills[].mcp_tools`` and the stable ``id`` are
        untouched and no cipher is needed. No-op when the profile is missing,
        non-dict, or an ACP profile (which carries no ``llm_profile_ref``).
        """
        path = self._get_profile_path(name)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        if data.get("agent_kind", "openhands") != "openhands":
            return
        data["llm_profile_ref"] = new_ref
        self._atomic_write(path, json.dumps(data, indent=2))

    def list_summaries(self) -> list[dict[str, Any]]:
        """Project profile metadata without instantiating secrets.

        Reads JSON directly and returns
        ``{id, name, agent_kind, revision, llm_profile_ref, mcp_server_refs}``
        per profile (``llm_profile_ref`` is ``None`` for ACP profiles). Files
        with invalid names, corrupt JSON, or non-dict top-level values are
        skipped with a warning.
        """
        summaries: list[dict[str, Any]] = []
        with self._acquire_lock():
            for path in sorted(self.base_dir.glob("*.json")):
                name = path.stem
                if not PROFILE_NAME_REGEX.match(name):
                    logger.warning(
                        f"[AgentProfile Store] Skipping invalid name {name!r}"
                    )
                    continue
                try:
                    data = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(
                        f"[AgentProfile Store] Skipping corrupted profile {name!r}: {e}"
                    )
                    continue
                if not isinstance(data, dict):
                    logger.warning(
                        f"[AgentProfile Store] Skipping non-dict profile {name!r}"
                    )
                    continue
                agent_kind = data.get("agent_kind", "openhands")
                summaries.append(
                    {
                        "id": data.get("id"),
                        "name": name,
                        "agent_kind": agent_kind,
                        "revision": data.get("revision"),
                        "llm_profile_ref": (
                            data.get("llm_profile_ref")
                            if agent_kind == "openhands"
                            else None
                        ),
                        "mcp_server_refs": data.get("mcp_server_refs"),
                    }
                )
        return summaries

    def name_for_id(self, profile_id: str | UUID) -> str | None:
        """Return the stored name for a stable profile id, or ``None`` if not found.

        Scans ``list_summaries()`` under the lock so the lookup is consistent
        with the on-disk state at the time of the call.  Mirrors the id→name
        resolution that used to be open-coded by each caller.
        """
        target = str(profile_id)
        for summary in self.list_summaries():
            if str(summary.get("id")) == target:
                return str(summary["name"])
        return None


@runtime_checkable
class AgentProfileStoreProtocol(Protocol):
    """Structural contract shared by :class:`AgentProfileStore` (file-backed) and
    any alternative backend — notably a multi-tenant DB store in the cloud
    app-server.

    The SDK's store-agnostic helpers — ``profile_refs`` (the LLM-profile FK) and
    :func:`save_profile_preserving_identity` — are typed against this Protocol so
    a non-file store reuses them verbatim instead of re-deriving the FK / id
    lifecycle. :meth:`lock` must be **re-entrant**: the FK helpers nest it around
    :meth:`list_summaries` / :meth:`set_llm_profile_ref`, and a cloud store whose
    router already holds a row-level transaction must treat a nested ``lock()``
    as a no-op rather than deadlocking.
    """

    def lock(self, timeout: float = ...) -> AbstractContextManager[None]: ...

    def list(self) -> list[str]: ...

    def list_summaries(self) -> list[dict[str, Any]]: ...

    def save(
        self,
        profile: OpenHandsAgentProfile | ACPAgentProfile,
        *,
        cipher: Cipher | None = ...,
        max_profiles: int | None = ...,
    ) -> None: ...

    def load(
        self, name: str, *, cipher: Cipher | None = ...
    ) -> OpenHandsAgentProfile | ACPAgentProfile: ...

    def delete(self, name: str) -> None: ...

    def rename(self, old_name: str, new_name: str) -> None: ...

    def set_llm_profile_ref(self, name: str, new_ref: str) -> None: ...

    def name_for_id(self, profile_id: str | UUID) -> str | None: ...


def _existing_identity(
    store: AgentProfileStoreProtocol, name: str
) -> tuple[UUID | None, int | None]:
    """Return the stored ``(id, revision)`` of the profile under ``name``.

    Reads :meth:`~AgentProfileStoreProtocol.list_summaries` (no secret
    instantiation). A malformed stored id is treated as "no prior identity".
    """
    for summary in store.list_summaries():
        if summary.get("name") != name:
            continue
        sid = summary.get("id")
        rev = summary.get("revision")
        try:
            parsed = UUID(str(sid)) if sid is not None else None
        except (ValueError, TypeError):
            parsed = None
        return parsed, rev if isinstance(rev, int) else None
    return None, None


def save_profile_preserving_identity(
    store: AgentProfileStoreProtocol,
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    cipher: Cipher | None = None,
    max_profiles: int | None = None,
) -> OpenHandsAgentProfile | ACPAgentProfile:
    """Save ``profile`` with the server-managed id/revision policy.

    Hoisted from the agent-server router so the file store and a cloud DB store
    stamp identity identically (the active pointer is keyed on the id):

    * **overwrite** a namesake → keep its stable ``id``, set
      ``revision = prev + 1``;
    * **create** → mint a fresh ``uuid4`` (ignoring any client-supplied id).

    The read → stamp → save runs under :meth:`~AgentProfileStoreProtocol.lock`
    so two concurrent creates of the same new name can't both mint an id and
    clobber each other. Returns the saved profile (final ``id`` / ``revision``).

    Raises:
        ProfileLimitExceeded: If ``max_profiles`` would be exceeded.
    """
    with store.lock():
        existing_id, existing_rev = _existing_identity(store, profile.name)
        if existing_id is not None:
            profile = profile.model_copy(
                update={"id": existing_id, "revision": (existing_rev or 0) + 1}
            )
        else:
            profile = profile.model_copy(update={"id": uuid4()})
        store.save(profile, cipher=cipher, max_profiles=max_profiles)
    return profile
