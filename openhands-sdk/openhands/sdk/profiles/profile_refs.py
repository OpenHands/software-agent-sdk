"""Foreign-key lifecycle between LLM profiles and ``AgentProfile``\\ s.

An ``OpenHandsAgentProfile.llm_profile_ref`` is a soft FK onto an LLM-profile
store key. ``find_referrers`` / ``cascade_rename`` / ``delete_llm_profile`` /
``rename_llm_profile`` keep that FK from dangling.

Store-agnostic: these touch the agent-profile store only through
:class:`~openhands.sdk.profiles.agent_profile_store.AgentProfileStoreProtocol`,
never the filesystem, so the file store and a cloud DB store reuse them verbatim.

Lock order: agent-profiles before llm-profiles (never the reverse — deadlock).
A guarded delete/rename holds the re-entrant agent-profiles ``lock()`` across the
whole scan→mutate window to close the TOCTOU. The FK covers ``llm_profile_ref``
only; ``mcp_server_refs`` are checked at resolve-time (#3717).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger
from openhands.sdk.profiles.agent_profile_store import PROFILE_NAME_REGEX


if TYPE_CHECKING:
    from openhands.sdk.llm.llm_profile_store import LLMProfileMutator
    from openhands.sdk.profiles.agent_profile_store import AgentProfileStoreProtocol

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


def _scan_referrers(
    store: AgentProfileStoreProtocol, llm_profile_name: str
) -> list[str]:
    """Return citing agent-profile names. Caller must hold :meth:`store.lock`.

    Reads metadata via ``list_summaries`` (no validation / secret
    instantiation). Only the OpenHands variant carries ``llm_profile_ref``.
    """
    return [
        str(summary["name"])
        for summary in store.list_summaries()
        if summary.get("agent_kind", "openhands") == "openhands"
        and summary.get("llm_profile_ref") == llm_profile_name
    ]


def _rewrite_refs(
    store: AgentProfileStoreProtocol, old_name: str, new_name: str
) -> list[str]:
    """Repoint every ``llm_profile_ref == old_name`` to ``new_name``.

    Caller must hold :meth:`store.lock`. Finds referrers via ``list_summaries``
    and applies the store's surgical ``set_llm_profile_ref`` write primitive per
    profile, so encrypted ``mcp_tools`` and the stable ``id`` are untouched and
    no cipher is needed. Returns the names of the rewritten profiles.
    """
    rewritten = _scan_referrers(store, old_name)
    for name in rewritten:
        store.set_llm_profile_ref(name, new_name)
    return rewritten


def find_referrers(
    store: AgentProfileStoreProtocol, llm_profile_name: str
) -> list[str]:
    """Names of the agent profiles whose ``llm_profile_ref == llm_profile_name``."""
    with store.lock():
        return _scan_referrers(store, llm_profile_name)


def cascade_rename(
    store: AgentProfileStoreProtocol, old_name: str, new_name: str
) -> list[str]:
    """Atomically repoint all ``llm_profile_ref == old_name`` to ``new_name``.

    Holds the agent-profiles lock for the whole scan-and-rewrite, so concurrent
    saves cannot interleave. Returns the rewritten profile names.
    """
    _validate_name(new_name)
    with store.lock():
        rewritten = _rewrite_refs(store, old_name, new_name)
    if rewritten:
        logger.info(
            f"[Profile FK] Cascaded llm_profile_ref `{old_name}` -> `{new_name}` "
            f"across {len(rewritten)} agent profile(s)."
        )
    return rewritten


def delete_llm_profile(
    agent_store: AgentProfileStoreProtocol,
    llm_store: LLMProfileMutator,
    llm_profile_name: str,
) -> None:
    """Delete an LLM profile only if no agent profile references it.

    Holds the agent-profiles lock across the referrer check and the delete, then
    delegates to ``llm_store.delete`` (which manages its own lock) — preserving
    the agent-profiles-before-llm-profiles order. Raises
    :class:`ProfileReferenced` naming the referrers if any exist.
    """
    with agent_store.lock():
        referrers = _scan_referrers(agent_store, llm_profile_name)
        if referrers:
            raise ProfileReferenced(referrers)
        llm_store.delete(llm_profile_name)


def rename_llm_profile(
    agent_store: AgentProfileStoreProtocol,
    llm_store: LLMProfileMutator,
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
    with agent_store.lock():
        llm_store.rename(old_name, new_name)
        return _rewrite_refs(agent_store, old_name, new_name)
