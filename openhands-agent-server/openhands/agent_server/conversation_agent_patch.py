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
    """The current step must finish before resources can be replaced."""


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
        private_servers = {
            **state.conversation_mcp_servers,
            **request.resources.mcp_servers,
        }
        available = {**conversation.agent.mcp_config, **private_servers}
        refs = updated_profile.mcp_server_refs
        missing = sorted(set(refs or ()) - available.keys())
        if missing:
            raise ValueError(f"Unknown MCP server references: {missing}")
        selected = (
            available if refs is None else {name: available[name] for name in refs}
        )
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
        if registry_changed:
            state.secret_registry = secret_registry
        try:
            if selected != conversation.agent.mcp_config:
                conversation.update_mcp_config(selected)
        except BaseException:
            if registry_changed:
                state.secret_registry = previous_registry
            raise
        if updated_profile.condenser != profile.condenser:
            new_agent = conversation.agent.model_copy(
                update={"condenser": new_condenser}
            )
            conversation.agent = new_agent
            state.agent = new_agent
            if new_condenser is not None:
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
