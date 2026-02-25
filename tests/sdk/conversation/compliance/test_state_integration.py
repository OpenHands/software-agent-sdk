"""Tests for ConversationState.add_event() integration with compliance monitoring."""

import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace
from tests.sdk.conversation.compliance.conftest import (
    make_action_event,
    make_observation_event,
    make_user_message_event,
)


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace for testing."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    return LocalWorkspace(working_dir=workspace_dir)


@pytest.fixture
def mock_agent():
    """Create a mock agent for testing."""
    llm = LLM(model="mock-model", api_key=SecretStr("fake-key"))
    return Agent(llm=llm)


@pytest.fixture
def conversation_state(temp_workspace, mock_agent):
    """Create a ConversationState for testing."""
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=mock_agent,
        workspace=temp_workspace,
        persistence_dir=None,  # In-memory for testing
    )


def test_add_event_appends_to_event_log(conversation_state):
    """add_event() should append events to the event log."""
    initial_count = len(conversation_state.events)

    user_msg = make_user_message_event("Hello")
    conversation_state.add_event(user_msg)

    assert len(conversation_state.events) == initial_count + 1
    assert conversation_state.events[-1].id == user_msg.id


def test_add_event_lazy_creates_monitor(conversation_state):
    """compliance_monitor should be lazily initialized."""
    # Initially None
    assert conversation_state._compliance_monitor is None

    # Access via property triggers creation
    monitor = conversation_state.compliance_monitor

    assert monitor is not None
    assert conversation_state._compliance_monitor is monitor


def test_add_event_checks_compliance(conversation_state):
    """add_event() should check compliance and detect violations."""
    # Add an action
    action = make_action_event(tool_call_id="call_1")
    conversation_state.add_event(action)

    # User message while action pending should create violation
    user_msg = make_user_message_event()
    conversation_state.add_event(user_msg)

    # Should have recorded violation
    assert conversation_state.compliance_monitor.violation_count > 0
    violations = conversation_state.compliance_monitor.violations
    assert any(v.property_name == "interleaved_message" for v in violations)


def test_add_event_normal_flow_no_violations(conversation_state):
    """Normal conversation flow should have no violations."""
    # Normal flow: action -> observation -> user message
    action = make_action_event(tool_call_id="call_1")
    conversation_state.add_event(action)

    obs = make_observation_event(action)
    conversation_state.add_event(obs)

    user_msg = make_user_message_event()
    conversation_state.add_event(user_msg)

    # No violations
    assert conversation_state.compliance_monitor.violation_count == 0


def test_add_event_still_adds_on_violation(conversation_state):
    """Events should still be added even when violations occur (observation mode)."""
    action = make_action_event(tool_call_id="call_1")
    conversation_state.add_event(action)

    # User message while action pending - violation
    user_msg = make_user_message_event()
    conversation_state.add_event(user_msg)

    # Event should still be in the log
    assert conversation_state.events[-1].id == user_msg.id

    # Violation should be recorded
    assert conversation_state.compliance_monitor.violation_count > 0


def test_add_event_tracks_state_correctly(conversation_state):
    """add_event() should correctly update compliance state."""
    action = make_action_event(tool_call_id="call_track")
    conversation_state.add_event(action)

    monitor = conversation_state.compliance_monitor
    assert "call_track" in monitor.state.pending_tool_call_ids
    assert "call_track" in monitor.state.all_tool_call_ids

    obs = make_observation_event(action)
    conversation_state.add_event(obs)

    assert "call_track" not in monitor.state.pending_tool_call_ids
    assert "call_track" in monitor.state.completed_tool_call_ids


def test_compliance_monitor_property_returns_same_instance(conversation_state):
    """compliance_monitor property should return the same instance each time."""
    monitor1 = conversation_state.compliance_monitor
    monitor2 = conversation_state.compliance_monitor

    assert monitor1 is monitor2
