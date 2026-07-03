"""Build the default :class:`~openhands.sdk.profiles.AgentProfile` from live settings.

The inverse of :func:`~openhands.sdk.profiles.resolve_agent_profile`: turns the
current ``AgentSettingsConfig`` into the single profile used by the one-time
migration (lazy on the local agent-server, eager backfill on the cloud one).
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    OpenHandsAgentProfile,
    build_profile_verification,
)


if TYPE_CHECKING:
    from openhands.sdk.settings import AgentSettingsConfig


# Name of the lazily-seeded default / migration profile and the soft LLM-ref
# fallback (a "default" LLM profile the resolver checks at materialize time).
SEED_PROFILE_NAME = "default"


def build_seed_profile(
    agent_settings: AgentSettingsConfig,
    active_llm_profile: str | None,
    *,
    name: str = SEED_PROFILE_NAME,
) -> OpenHandsAgentProfile | ACPAgentProfile:
    """Build one behavior-preserving ``AgentProfile`` from ``agent_settings``.

    Branches on ``agent_kind`` so an ACP setup seeds an ACP profile.
    ``mcp_server_refs=None`` exposes all of the user's MCP servers; an OpenHands
    profile references ``active_llm_profile``, falling back to
    ``SEED_PROFILE_NAME`` (a soft ref the resolver checks at materialize time).
    """
    if agent_settings.agent_kind == "acp":
        return ACPAgentProfile(
            name=name,
            acp_server=agent_settings.acp_server,
            acp_model=agent_settings.acp_model,
            acp_session_mode=agent_settings.acp_session_mode,
            acp_prompt_timeout=agent_settings.acp_prompt_timeout,
            # Settings store the command as a token list; the profile holds a
            # single (re-parseable) string. Empty list => use the server default.
            acp_command=(
                shlex.join(agent_settings.acp_command)
                if agent_settings.acp_command
                else None
            ),
            acp_args=list(agent_settings.acp_args) or None,
            mcp_server_refs=None,
            # ACP default; explicit for symmetry with the OpenHands seed below.
            skill_refs=[],
        )
    context = agent_settings.agent_context
    return OpenHandsAgentProfile(
        name=name,
        llm_profile_ref=active_llm_profile or SEED_PROFILE_NAME,
        agent=agent_settings.agent,
        # Copy verbatim (None = server default) so a global config with e.g.
        # browser or client tools seeds a genuinely behavior-preserving profile.
        tools=agent_settings.tools,
        # Embed the global's already-resolved skills + skill_refs=[] (no further
        # discovery) to reproduce its exact set; skill_refs=None would re-discover
        # user+public and inject skills a partial-source global never had.
        skills=list(context.skills),
        skill_refs=[],
        system_message_suffix=context.system_message_suffix,
        condenser=agent_settings.condenser,
        verification=build_profile_verification(agent_settings.verification),
        enable_sub_agents=agent_settings.enable_sub_agents,
        enable_switch_llm_tool=agent_settings.enable_switch_llm_tool,
        tool_concurrency_limit=agent_settings.tool_concurrency_limit,
        mcp_server_refs=None,
    )
