"""Tests for ``_compose_conversation_info`` lifting ACP model state.

The chain is:

  1. ``ACPAgent._init`` captures the session's ``current_model_id`` and
     ``available_models`` into PrivateAttrs (because ``AgentBase`` is frozen).
  2. PrivateAttrs don't survive ``model_dump``, so the values can't ride out
     on the serialized ``agent`` field of the API response.
  3. The agent-server lifts them off the live agent instance into top-level
     ``current_model_id`` / ``available_models`` fields on ``ConversationInfo``
     so the downstream OpenHands app_server (chip) and the model picker can
     read them — the picker resolves the id to a label from the list itself.

These tests pin down step 3 — that ``_compose_conversation_info`` reads the
attributes off the agent (falling back to persisted ``agent_state``) and
routes them into the response model.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import _compose_conversation_info
from openhands.agent_server.models import ConversationInfo, StoredConversation
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.acp_models import ACPModelInfo
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


def test_cold_read_falls_back_to_acp_model_override():
    """Cold list read (``init_state`` not fired): with no live/persisted current
    id, the serialized ``acp_model`` is the best last-known hint and is surfaced.
    """
    agent = ACPAgent(acp_command=["echo", "test"], acp_model="model-x")
    # _initialized defaults to False (cold read); _current_model_id defaults None.
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "model-x"


def test_live_agent_does_not_fall_back_to_unapplied_override():
    """Live initialized agent whose override was NOT applied (e.g. a resume whose
    ``set_session_model`` the server rejected): ``current_model_id`` is the
    authoritative ``None`` and must NOT fall back to ``acp_model`` — that would
    re-assert an override the live session isn't running.
    """
    agent = ACPAgent(acp_command=["echo", "test"], acp_model="model-x")
    # init_state has fired; the override resolved to None (rejected) and was
    # recorded as not applied. The persisted hint was cleared by init_state.
    agent._initialized = True
    agent._current_model_id = None
    agent._model_override_applied = False
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id is None


def test_available_models_lifted_from_acp_agent():
    """``ConversationInfo.available_models`` mirrors the agent's model list.

    The list is surfaced verbatim (as normalized ``ACPModelInfo``) so the
    client can render a picker and resolve ``current_model_id`` to a label
    itself — the server does no name curation. Simulates claude-agent-acp's
    default config where ``current_model_id`` is the opaque alias ``"default"``
    and the readable identity lives in the matching ``available_models`` entry.
    """
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "default"
    agent._available_models = [
        ACPModelInfo(
            model_id="default",
            name="Default (recommended)",
            description="Opus 4.7 with 1M context · Most capable for complex work",
        ),
        ACPModelInfo(model_id="sonnet", name="Sonnet", description="Sonnet 4.6"),
    ]
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "default"
    assert info.available_models == agent._available_models


def test_available_models_empty_when_server_omits_them():
    """Servers that don't surface the UNSTABLE ``models`` capability yield []."""
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "gpt-5.5/xhigh"
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "gpt-5.5/xhigh"
    assert info.available_models == []


def test_available_models_empty_for_native_openhands_agent():
    """Native agents don't have the attribute; the lift yields []."""
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

    assert info.available_models == []


def test_current_model_fields_read_from_persisted_agent_state():
    """Cold conversation list: the live agent's PrivateAttrs are still
    None/empty because ``init_state`` hasn't fired, but ``agent_state``
    persisted the values from the last session.  The lift should source from
    there so the chip + picker survive cold reads.  The persisted
    ``available_models`` is a list of plain dicts; ``ConversationInfo``
    coerces it back into ``ACPModelInfo``.
    """
    agent = ACPAgent(acp_command=["echo", "test"])
    # Crucially, leave the PrivateAttrs at their defaults — this simulates an
    # agent freshly reconstructed from persisted JSON before any ``init_state``.
    state = _make_state(agent)
    state.agent_state = {
        "acp_session_id": "prior-session",
        "acp_current_model_id": "default",
        "acp_available_models": [
            {
                "model_id": "default",
                "name": "Default (recommended)",
                "description": "Opus 4.7 with 1M context",
            }
        ],
    }
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "default"
    assert info.available_models == [
        ACPModelInfo(
            model_id="default",
            name="Default (recommended)",
            description="Opus 4.7 with 1M context",
        )
    ]


def test_live_agent_attrs_take_precedence_over_persisted_state():
    """Within an active session, the live agent is the freshest source."""
    agent = ACPAgent(acp_command=["echo", "test"])
    agent._current_model_id = "claude-opus-4-1"
    agent._available_models = [
        ACPModelInfo(model_id="claude-opus-4-1", name="Opus 4.1")
    ]
    state = _make_state(agent)
    # Stale persisted state from a prior session that picked a different model.
    state.agent_state = {
        "acp_current_model_id": "haiku",
        "acp_available_models": [{"model_id": "haiku", "name": "Haiku"}],
    }
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "claude-opus-4-1"
    assert info.available_models == [
        ACPModelInfo(model_id="claude-opus-4-1", name="Opus 4.1")
    ]


def test_current_model_id_falls_back_to_acp_model_on_cold_read():
    """Cold read of a switched/overridden conversation whose server never
    surfaced ``models``: no live PrivateAttr and no persisted hint, but
    ``acp_model`` is a real serialized field and is authoritative — so the chip
    still resolves instead of going blank.
    """
    agent = ACPAgent(acp_command=["echo", "test"], acp_model="enterprise-x")
    # Leave _current_model_id at None and agent_state empty: pure cold read.
    state = _make_state(agent)
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.current_model_id == "enterprise-x"


def test_supports_runtime_model_switch_lifted_from_agent_state():
    """The static provider capability is read from persisted ``agent_state``
    (written at session init), so it's correct on cold list reads too.
    """
    agent = ACPAgent(acp_command=["echo", "test"])
    state = _make_state(agent)
    state.agent_state = {"acp_supports_runtime_model_switch": True}
    stored = _make_stored(state)

    info = _compose_conversation_info(stored, state)

    assert info.supports_runtime_model_switch is True


def test_supports_runtime_model_switch_defaults_false():
    """No hint (native agent, or ACP conversation that hasn't started) -> False."""
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

    assert info.supports_runtime_model_switch is False
