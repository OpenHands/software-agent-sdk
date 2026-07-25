"""Integration-like tests for agent-server conversation resume semantics.

These are the agent-server counterpart to ``test_conversation_restore_behavior``
(which documents the *SDK* ``LocalConversation`` restore contract, where the
runtime-provided agent config wins). Here the caller is the agent server, which
rebuilds the runtime agent from its ``meta.json`` snapshot — a snapshot taken at
*creation* and never re-written when a live ``switch_llm`` / ``switch_profile`` /
``switch_acp_model`` mutates the agent. Those switches persist only to
``base_state.json``. So on an agent-server restart, ``base_state.json`` — not the
stale ``meta.json`` — must be authoritative for the live-mutable agent config,
or the switch silently reverts (issue #4032).

Spec, exercised end-to-end through ``ConversationService`` (start -> switch ->
tear down -> restart over the same dir -> hydrate via the real lazy-resume path):

- A live ``switch_llm`` (regular agent) survives a restart: ``llm`` (and its
  ``timeout``) comes back switched, not reverted to the creation-time value.
- A ``switch_acp_model`` (ACP agent) survives a restart: ``acp_model`` comes back
  switched, with no write-side mirror into ``meta.json``.
"""

from pathlib import Path

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.agent import ACPAgent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def _request(agent, workspace_dir: Path) -> StartConversationRequest:
    return StartConversationRequest(
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )


@pytest.mark.asyncio
async def test_switched_llm_survives_agent_server_restart(tmp_path):
    """Issue #4032: a live ``switch_llm`` persists only to base_state.json.

    Before the fix the server rebuilt the resume agent purely from the stale
    ``meta.json`` snapshot, so the switched LLM — and its ``timeout`` — reverted
    to the creation-time value after a restart. base_state.json must win for the
    live-mutable ``llm`` on resume.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = _request(
        Agent(llm=LLM(model="gpt-4o", usage_id="test-llm", timeout=300), tools=[]),
        workspace_dir,
    )

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)
        conversation_id = conversation_info.id

        # Switch the live LLM to timeout=600 (distinct usage_id so the registry
        # installs it rather than reusing the first-write-wins cached entry).
        # This writes ConversationState.agent -> base_state.json only.
        event_service = await primary.get_event_service(conversation_id)
        assert event_service is not None
        conversation = event_service.get_conversation()
        conversation.switch_llm(
            LLM(model="gpt-4o", usage_id="test-llm-switched", timeout=600)
        )
        assert conversation.state.agent.llm.timeout == 600

    # Restart: a fresh service over the same directory hydrates from disk.
    async with ConversationService(conversations_dir=conversations_dir) as restarted:
        assert restarted._event_services is not None
        assert conversation_id not in restarted._event_services
        restarted_event_service = await restarted.get_event_service(conversation_id)
        assert restarted_event_service is not None
        restarted_conversation = restarted_event_service.get_conversation()
        # The switch survives the restart instead of reverting to timeout=300.
        assert restarted_conversation.state.agent.llm.timeout == 600


@pytest.mark.asyncio
async def test_switched_acp_model_survives_agent_server_restart(tmp_path):
    """A pre-session ``switch_acp_model`` persists only to base_state.json.

    ACP's live-switchable field is ``acp_model`` (``model_post_init`` re-derives
    the sentinel ``llm.model`` from it). The switch defers before the first run
    and persists to base_state.json; on resume the server must read it back from
    there rather than from the stale ``meta.json`` snapshot — the same rule as
    the regular LLM path, so no ``meta.json`` write-mirror is needed.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = _request(
        ACPAgent(acp_command=["echo", "test"], acp_model="model-a"),
        workspace_dir,
    )

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)
        conversation_id = conversation_info.id

        event_service = await primary.get_event_service(conversation_id)
        assert event_service is not None
        conversation = event_service.get_conversation()
        # No live session yet: switch_acp_model defers and persists model-b to
        # base_state.json (no protocol round-trip, no subprocess).
        conversation.switch_acp_model("model-b")
        acp_agent = conversation.state.agent
        assert isinstance(acp_agent, ACPAgent)
        assert acp_agent.acp_model == "model-b"

    async with ConversationService(conversations_dir=conversations_dir) as restarted:
        restarted_event_service = await restarted.get_event_service(conversation_id)
        assert restarted_event_service is not None
        restarted_agent = restarted_event_service.get_conversation().state.agent
        assert isinstance(restarted_agent, ACPAgent)
        # The switch survives the restart instead of reverting to model-a.
        assert restarted_agent.acp_model == "model-b"
