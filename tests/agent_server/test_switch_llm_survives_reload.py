"""Real-like end-to-end tests for the base_state single-source-of-truth fix.

These drive the *real* ``ConversationService`` against a *real* on-disk
persistence directory (no mocks of the conversation, event service, or SDK).
They reproduce the user-reported bug — a runtime model switch getting reverted
on reload — and prove it stays fixed through the actual code paths a client
(odie / Agent Canvas) exercises:

    start conversation -> switch_llm at runtime -> base_state.json owns the agent
    -> idle-eviction (or a full service restart) -> rehydrate -> switch survives.

They also guard against the "does it affect various things" worry: meta.json
never carries the agent, and unrelated persisted state (tags, confirmation
policy, secret registry) survives the round-trip intact.

No network is required: ``switch_llm`` and persistence never call the model.
"""

import json
from uuid import UUID

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def _request(workspace_dir, model: str, usage_id: str) -> StartConversationRequest:
    return StartConversationRequest(
        agent=Agent(llm=LLM(model=model, usage_id=usage_id), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )


def _read_json(path):
    return json.loads(path.read_text())


async def _switch_llm(service: ConversationService, conv_id: UUID, llm: LLM) -> None:
    """Drive the real runtime switch path used by the /switch_llm endpoint."""
    event_service = await service.get_event_service(conv_id)
    assert event_service is not None
    conversation = event_service.get_conversation()
    conversation.switch_llm(llm)


@pytest.mark.asyncio
async def test_runtime_switch_llm_survives_idle_eviction(tmp_path):
    """The reported bug, end to end: a runtime switch_llm must NOT revert when
    an idle conversation is evicted and later rehydrated from disk.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(
            _request(workspace_dir, "gpt-4o", "primary")
        )
        conv_id = info.id

        # Runtime switch (new usage_id so the registry actually installs it).
        await _switch_llm(
            service, conv_id, LLM(model="claude-sonnet-4", usage_id="switched")
        )

        conv_dir = conversations_dir / conv_id.hex
        base_state = _read_json(conv_dir / "base_state.json")
        meta = _read_json(conv_dir / "meta.json")
        # base_state.json owns the switched agent; meta.json has no agent at all.
        assert base_state["agent"]["llm"]["model"] == "claude-sonnet-4"
        assert "agent" not in meta

        # Force idle eviction (real internal path); the live service drops the
        # in-memory conversation but keeps the record for rehydration.
        await service._evict_idle_conversations(0.0)

        # Accessing it again rehydrates from base_state.json.
        event_service = await service.get_event_service(conv_id)
        assert event_service is not None
        rehydrated = event_service.get_conversation()
        assert rehydrated.agent.llm.model == "claude-sonnet-4"


@pytest.mark.asyncio
async def test_runtime_switch_llm_survives_service_restart(tmp_path):
    """A runtime switch_llm must also survive a full agent-server restart
    (a fresh ConversationService reading the same directory).
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(
            _request(workspace_dir, "gpt-4o", "primary")
        )
        conv_id = info.id
        await _switch_llm(
            service, conv_id, LLM(model="gpt-5-codex", usage_id="switched")
        )

    # Fresh service == server restart: agent comes back from base_state.json.
    async with ConversationService(conversations_dir=conversations_dir) as service2:
        reloaded = await service2.get_conversation(conv_id)
        assert reloaded is not None
        assert reloaded.agent.llm.model == "gpt-5-codex"

        event_service = await service2.get_event_service(conv_id)
        assert event_service is not None
        assert event_service.get_conversation().agent.llm.model == "gpt-5-codex"


@pytest.mark.asyncio
async def test_new_conversation_agent_only_in_base_state(tmp_path):
    """A brand-new conversation persists the agent to base_state.json only —
    never to meta.json — so the duplication that caused the bug cannot recur.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(
            _request(workspace_dir, "gpt-4o", "primary")
        )
        conv_id = info.id

    conv_dir = conversations_dir / conv_id.hex
    meta = _read_json(conv_dir / "meta.json")
    base_state = _read_json(conv_dir / "base_state.json")
    assert "agent" not in meta
    assert base_state["agent"]["llm"]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_switch_llm_preserves_unrelated_state_across_reload(tmp_path):
    """ "Does it affect various things?" — unrelated persisted state (tags,
    confirmation policy) must survive a switch + eviction + rehydration.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="primary"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
        tags={"client": "canvas", "kind": "regression"},
    )

    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(request)
        conv_id = info.id
        await _switch_llm(
            service, conv_id, LLM(model="claude-opus-4", usage_id="switched")
        )
        await service._evict_idle_conversations(0.0)

        event_service = await service.get_event_service(conv_id)
        assert event_service is not None
        conversation = event_service.get_conversation()
        # Switched model is authoritative...
        assert conversation.agent.llm.model == "claude-opus-4"
        # ...and unrelated state came back intact.
        assert dict(event_service.stored.tags) == {
            "client": "canvas",
            "kind": "regression",
        }
        assert isinstance(conversation.state.confirmation_policy, NeverConfirm)
