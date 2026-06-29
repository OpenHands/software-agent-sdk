"""Foreign-key lifecycle between LLM profiles and ``AgentProfile``\\ s.

An ``OpenHandsAgentProfile.llm_profile_ref`` is a soft FK onto an LLM-profile
store key. This module keeps that FK from dangling:

* :func:`find_referrers` — which agent profiles cite a given LLM profile.
* :func:`cascade_rename` — rewrite every matching ``llm_profile_ref`` in lock-step
  with an LLM-profile rename.
* :func:`delete_llm_profile` / :func:`rename_llm_profile` — guarded cross-store
  operations that raise :class:`ProfileReferenced` (routers map → 409) or cascade.

Lock acquisition order — **agent-profiles, then llm-profiles.**
The two stores are HOME-rooted but independent (each has its own file lock).
A guarded delete/rename holds the *agent-profiles* lock across the whole
"scan referrers → mutate the LLM profile" window, so no concurrent
``AgentProfileStore.save`` can introduce a new referrer between the check and
the mutation (the TOCTOU this module exists to close). Always take the
agent-profiles lock first; never the reverse, or two callers could deadlock.

Scope: the FK covers ``llm_profile_ref`` only. ``mcp_server_refs`` are keys into
the user's independently-mutable global ``mcp_config`` and are checked at
resolve-time (#3717), not constrained here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger
from openhands.sdk.profiles.agent_profile_store import PROFILE_NAME_REGEX


if TYPE_CHECKING:
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore
    from openhands.sdk.profiles.agent_profile_store import AgentProfileStore

logger = get_logger(__name__)


class ProfileReferenced(Exception):
    """Raised when an LLM profile cannot be deleted because agent profiles cite it.

    ``referrers`` is the list of citing agent-profile names; routers surface it
    in a 409 so the user knows what to detach first.
    """

    def __init__(self, referrers: list[str]) -> None:
        self.referrers = list(referrers)
        joined = ", ".join(self.referrers) or "<none>"
        super().__init__(
            f"LLM profile is referenced by {len(self.referrers)} agent "
            f"profile(s): {joined}"
        )


def _validate_name(name: str) -> None:
    """Reject names that are not legal profile keys (path traversal etc.)."""
    if not PROFILE_NAME_REGEX.match(name):
        raise ValueError(f"Invalid profile name: {name!r}.")


def _scan_referrers(store: AgentProfileStore, llm_profile_name: str) -> list[str]:
    """Return citing agent-profile names. Caller must hold the store lock.

    Reads JSON directly (no validation/secret instantiation). Non-dict and
    corrupt files are skipped.
    """
    referrers: list[str] = []
    for path in sorted(store.base_dir.glob("*.json")):
        if not PROFILE_NAME_REGEX.match(path.stem):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        # agent_kind defaults to "openhands"; only that variant has the ref.
        if data.get("agent_kind", "openhands") != "openhands":
            continue
        if data.get("llm_profile_ref") == llm_profile_name:
            referrers.append(path.stem)
    return referrers


def _rewrite_refs(store: AgentProfileStore, old_name: str, new_name: str) -> list[str]:
    """Repoint every ``llm_profile_ref == old_name`` to ``new_name``.

    Caller must hold the store lock. Surgical raw-JSON edit: only the ref field
    changes, so encrypted ``mcp_tools`` and the stable ``id`` are untouched and
    no cipher is needed. Returns the names of the rewritten profiles.
    """
    rewritten: list[str] = []
    for path in sorted(store.base_dir.glob("*.json")):
        if not PROFILE_NAME_REGEX.match(path.stem):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("agent_kind", "openhands") != "openhands":
            continue
        if data.get("llm_profile_ref") != old_name:
            continue
        data["llm_profile_ref"] = new_name
        store._atomic_write(path, json.dumps(data, indent=2))
        rewritten.append(path.stem)
    return rewritten


def find_referrers(store: AgentProfileStore, llm_profile_name: str) -> list[str]:
    """Names of the agent profiles whose ``llm_profile_ref == llm_profile_name``."""
    with store._acquire_lock():
        return _scan_referrers(store, llm_profile_name)


def cascade_rename(store: AgentProfileStore, old_name: str, new_name: str) -> list[str]:
    """Atomically repoint all ``llm_profile_ref == old_name`` to ``new_name``.

    Holds the agent-profiles lock for the whole scan-and-rewrite, so concurrent
    saves cannot interleave. Returns the rewritten profile names.
    """
    _validate_name(new_name)
    with store._acquire_lock():
        rewritten = _rewrite_refs(store, old_name, new_name)
    if rewritten:
        logger.info(
            f"[Profile FK] Cascaded llm_profile_ref `{old_name}` -> `{new_name}` "
            f"across {len(rewritten)} agent profile(s)."
        )
    return rewritten


def delete_llm_profile(
    agent_store: AgentProfileStore,
    llm_store: LLMProfileStore,
    llm_profile_name: str,
) -> None:
    """Delete an LLM profile only if no agent profile references it.

    Holds the agent-profiles lock across the referrer check and the delete, then
    delegates to ``llm_store.delete`` (which manages its own lock) — preserving
    the agent-profiles-before-llm-profiles order. Raises
    :class:`ProfileReferenced` naming the referrers if any exist.
    """
    with agent_store._acquire_lock():
        referrers = _scan_referrers(agent_store, llm_profile_name)
        if referrers:
            raise ProfileReferenced(referrers)
        llm_store.delete(llm_profile_name)


def rename_llm_profile(
    agent_store: AgentProfileStore,
    llm_store: LLMProfileStore,
    old_name: str,
    new_name: str,
) -> list[str]:
    """Rename an LLM profile and cascade the rename to its referrers.

    Holds the agent-profiles lock across the whole operation, then delegates to
    ``llm_store.rename`` (which manages its own lock) — preserving the
    agent-profiles-before-llm-profiles order. The LLM file is renamed first, so
    if it fails (missing source / taken target) no refs are rewritten. Returns
    the rewritten agent-profile names.
    """
    _validate_name(new_name)
    with agent_store._acquire_lock():
        llm_store.rename(old_name, new_name)
        return _rewrite_refs(agent_store, old_name, new_name)
