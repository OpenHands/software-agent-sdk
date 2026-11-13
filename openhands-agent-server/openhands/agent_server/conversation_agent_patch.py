"""Apply a profile-shaped edit to one running OpenHands conversation."""

from openhands.agent_server.models import (
    UpdateConversationAgentRequest,
    UpdateConversationAgentResponse,
)
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.profiles.agent_profile import OpenHandsAgentProfile
from openhands.sdk.profiles.conversation_patch import apply_conversation_profile_diff


class StepInProgress(RuntimeError):
    """The current step must finish before resources can be updated."""


def apply_conversation_agent_patch(
    conversation: LocalConversation, request: UpdateConversationAgentRequest
) -> UpdateConversationAgentResponse:
    state = conversation.state
    with state:
        if conversation._step_in_progress:
            raise StepInProgress()
        profile = state.conversation_profile
        if not isinstance(profile, OpenHandsAgentProfile) or not isinstance(
            conversation.agent, Agent
        ):
            raise ValueError("This conversation has no editable OpenHands profile")
        updated_profile = apply_conversation_profile_diff(profile, request.profile_diff)
        existing = conversation.agent.mcp_config
        duplicate = request.resources.mcp_servers.keys() & (
            existing.keys() | state.conversation_mcp_servers.keys()
        )
        if duplicate:
            raise ValueError(f"MCP server already exists: {sorted(duplicate)}")
        private_servers = {
            **state.conversation_mcp_servers,
            **request.resources.mcp_servers,
        }
        available = {**existing, **private_servers}
        refs = updated_profile.mcp_server_refs
        missing = sorted(set(refs or ()) - available.keys())
        if missing:
            raise ValueError(f"Unknown MCP server references: {missing}")
        if "mcp_server_refs" in request.profile_diff and refs is not None:
            removed = existing.keys() - set(refs)
            if removed:
                raise ValueError(
                    f"MCP profile edits cannot remove connections: {sorted(removed)}"
                )
        selected = (
            available if refs is None else {name: available[name] for name in refs}
        )
        additions = {
            name: server for name, server in selected.items() if name not in existing
        }
        if len(additions) > 1:
            raise ValueError("Add only one MCP server per profile edit")
        secrets = request.resources.secrets
        denied = sorted(
            name
            for name in secrets
            if updated_profile.secret_refs is not None
            and name not in updated_profile.secret_refs
        )
        if denied:
            raise ValueError(f"Secret names not allowed by profile: {denied}")
        new_condenser = updated_profile.condenser.build_condenser(
            conversation.agent.llm
        )
        allowed = updated_profile.secret_refs
        secret_registry = state.secret_registry.model_copy(
            update={
                "secret_sources": {
                    name: source
                    for name, source in state.secret_registry.secret_sources.items()
                    if allowed is None or name in allowed
                }
            }
        )
        secret_registry.update_secrets(secrets)
        previous_registry = state.secret_registry
        registry_changed = (
            secret_registry.secret_sources != previous_registry.secret_sources
        )
        policy_changed = updated_profile.mcp_tool_policy != profile.mcp_tool_policy
        gateway = conversation.mcp_gateway if policy_changed else None
        if registry_changed:
            state.secret_registry = secret_registry
        try:
            if gateway is not None:
                gateway.set_tool_policy(updated_profile.mcp_tool_policy)
            if additions:
                conversation.add_mcp_servers(additions)
        except BaseException:
            try:
                if gateway is not None:
                    gateway.set_tool_policy(profile.mcp_tool_policy)
            finally:
                if registry_changed:
                    state.secret_registry = previous_registry
            raise
        agent_updates = {}
        if policy_changed:
            agent_updates["mcp_tool_policy"] = updated_profile.mcp_tool_policy
        if updated_profile.condenser != profile.condenser:
            agent_updates["condenser"] = new_condenser
        if agent_updates:
            new_agent = conversation.agent.model_copy(update=agent_updates)
            conversation.agent = state.agent = new_agent
            if (
                updated_profile.condenser != profile.condenser
                and new_condenser is not None
            ):
                llm = getattr(new_condenser, "llm", None)
                if (
                    llm is not None
                    and llm.usage_id not in conversation.llm_registry.usage_to_llm
                ):
                    conversation.llm_registry.add(llm)
        state.conversation_mcp_servers = private_servers
        state.conversation_profile = updated_profile
        state.conversation_profile_revision += 1
        return UpdateConversationAgentResponse(
            conversation_profile_revision=state.conversation_profile_revision,
            conversation_profile=updated_profile,
        )
