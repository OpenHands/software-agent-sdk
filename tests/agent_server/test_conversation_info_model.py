"""Tests for ``_compose_conversation_info`` lifting ``current_model_id``.

The chain is:

  1. ``ACPAgent._init`` writes the resolved model into ``_current_model_id``
     (a PrivateAttr, because ``AgentBase`` is frozen).
  2. PrivateAttrs don't survive ``model_dump``, so the value can't ride out
     on the serialized ``agent`` field of the API response.
  3. The agent-server lifts the value off the live agent instance into a
     top-level ``current_model_id`` field on ``ConversationInfo`` so the
     downstream OpenHands app_server can read it.

These tests pin down step 3 — that ``_compose_conversation_info`` actually
calls ``getattr(state.agent, "current_model_id", None)`` and routes the
result into the response model.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import _compose_conversation_info
from openhands.agent_server.models import ConversationInfo, StoredConversation
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def _make_state(agent) -> ConversationState:
    workspace = LocalWorkspace(working_dir="/tmp/test")
    return ConversationState(
        id=uuid4(),
        agent=agent,
        workspace=workspace,
        execution_status=ConversationExecutionStatus.IDLE,
        confirmation_policy=NeverConfirm(),
    )


def _make_stored(state: ConversationState) -> StoredConversation:
    # ``state.workspace`` is typed as the ``BaseWorkspace`` parent; we
    # constructed the state with a ``LocalWorkspace`` so it's safe to pass
    # through.  ``cast`` would be more correct but is noise for a test
    # helper — reconstruct the LocalWorkspace from the working dir instead.
    workspace = LocalWorkspace(working_dir=state.workspace.working_dir)
    return StoredConversation(
        id=state.id,
        agent=state.agent,
        workspace=workspace,
        title="Test",
        metrics=None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def test_current_model_id_is_lifted_from_acp_agent():
    """When the ACP agent has resolved a model, it surfaces on the response."""
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "claude-opus-4-1"
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert isinstance(info, ConversationInfo)
    assert info.current_model_id == "claude-opus-4-1"


def test_current_model_id_is_none_when_acp_agent_has_no_model():
    """Older ACP servers don't surface the field — we propagate ``None``."""
    agent = ACPAgent(acp_command=["echo", "test"])
    # ``_current_model_id`` defaults to None — leave it as-is.
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id is None


def test_current_model_id_is_none_for_native_openhands_agent():
    """Native agents don't have the attribute; ``getattr`` returns None."""
    agent = Agent(
        llm=LLM(
            model="gpt-4o",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        ),
        tools=[Tool(name="TerminalTool")],
    )
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    # Consumers should read ``agent.llm.model`` for native agents instead.
    assert info.current_model_id is None


@pytest.mark.parametrize(
    "override_model,server_model,expected",
    [
        # Caller forced a model via ``acp_model`` — that wins (mirrors _init).
        ("gpt-5", "claude-sonnet-4-5", "gpt-5"),
        # No override, server reports a model — use the server's value.
        (None, "claude-sonnet-4-5", "claude-sonnet-4-5"),
        # Neither side has a model — pass None through.
        (None, None, None),
    ],
)
def test_current_model_id_propagates_init_resolution(
    override_model, server_model, expected
):
    """End-to-end check of the resolution semantics ``_init`` is meant to apply.

    ``_init`` resolves ``self.acp_model or _extract_session_models(response)[0]``;
    the resolved value lands in ``_current_model_id``; the agent-server then
    lifts it onto ``ConversationInfo``. This test simulates the assignment
    ``_init`` makes and verifies the value travels through.
    """
    agent = ACPAgent(acp_command=["echo", "test"], acp_model=override_model)
    # Mirror the assignment that happens at the end of ``_init``.
    agent._current_model_id = override_model or server_model
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)
    assert info.current_model_id == expected


def _model_info(
    model_id: str, name: str | None, description: str | None = None
) -> object:
    m = MagicMock()
    m.model_id = model_id
    m.name = name
    m.description = description
    return m


def test_current_model_name_is_lifted_alongside_id():
    """``ConversationInfo.current_model_name`` mirrors the agent's resolved name.

    Simulates the claude-agent-acp default-config case: ``current_model_id``
    is the alias ``"default"`` and ``current_model_name`` is the resolved
    underlying model from ``ModelInfo.description`` (``"Opus 4.7 with 1M
    context"``) — neither ``modelId`` nor ``name`` carry the actual model
    identity for that case.
    """
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "default"
    agent._available_models = [
        _model_info(
            "default",
            "Default (recommended)",
            "Opus 4.7 with 1M context · Most capable for complex work",
        )
    ]
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "default"
    assert info.current_model_name == "Opus 4.7 with 1M context"


def test_current_model_name_falls_back_to_id_when_no_match():
    """codex-acp pattern: concrete id, no ``available_models`` lookup needed.

    Locks in the contract that ``current_model_name`` is always at least
    as informative as ``current_model_id``.
    """
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "gpt-5.5/xhigh"
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "gpt-5.5/xhigh"
    assert info.current_model_name == "gpt-5.5/xhigh"


def test_current_model_name_is_none_for_native_openhands_agent():
    """Native agents don't have the attribute; ``getattr`` returns None."""
    agent = Agent(
        llm=LLM(
            model="gpt-4o",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        ),
        tools=[Tool(name="TerminalTool")],
    )
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_name is None


def test_current_model_fields_read_from_persisted_agent_state():
    """Cold conversation list: the live agent's PrivateAttrs are still None
    because ``init_state`` hasn't fired, but ``agent_state`` persisted the
    values from the last session.  The lift should source from there so the
    chip survives cold reads.
    """
    agent = ACPAgent(acp_command=["echo", "test"])
    # Crucially, leave ``_current_model_id`` at its None default — this
    # simulates an agent freshly reconstructed from the persisted JSON
    # before any ``init_state`` call has run for this conversation.
    state = _make_state(agent)
    state.agent_state = {
        "acp_session_id": "prior-session",
        "acp_current_model_id": "default",
        "acp_current_model_name": "Default (recommended)",
    }
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "default"
    assert info.current_model_name == "Default (recommended)"


def test_live_agent_attrs_take_precedence_over_persisted_state():
    """Within an active session, the live agent is the freshest source."""
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "claude-opus-4-1"
    m = MagicMock()
    m.model_id = "claude-opus-4-1"
    m.name = "Opus 4.1"
    agent._available_models = [m]
    state = _make_state(agent)
    # Stale persisted state from a prior session that picked a different model.
    state.agent_state = {
        "acp_current_model_id": "haiku",
        "acp_current_model_name": "Haiku",
    }
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "claude-opus-4-1"
    assert info.current_model_name == "Opus 4.1"
