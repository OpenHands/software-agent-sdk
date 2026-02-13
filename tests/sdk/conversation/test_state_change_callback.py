"""Tests for ConversationState callback mechanism."""

import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def state():
    """Create a ConversationState for testing."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm)
    workspace = LocalWorkspace(working_dir="/tmp/test")

    state = ConversationState(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir="/tmp/test/.state",
        agent=agent,
    )

    # Set up filestore and enable autosave so callbacks are triggered
    state._fs = InMemoryFileStore()
    state._autosave_enabled = True

    return state


def test_set_on_state_change_callback(state):
    """Test that callback can be set and is called when state changes."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    # Set the callback
    state.set_on_state_change(callback)

    # Change state - should trigger callback
    with state:
        state.execution_status = ConversationExecutionStatus.RUNNING

    # Verify callback was called
    assert len(callback_calls) == 1
    event = callback_calls[0]
    assert isinstance(event, ConversationStateUpdateEvent)
    assert event.key == "execution_status"
    assert event.value == ConversationExecutionStatus.RUNNING


def test_callback_called_multiple_times(state):
    """Test that callback is called for multiple state changes."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    state.set_on_state_change(callback)

    # Make multiple state changes
    with state:
        state.execution_status = ConversationExecutionStatus.RUNNING
        state.execution_status = ConversationExecutionStatus.PAUSED
        state.execution_status = ConversationExecutionStatus.FINISHED

    # Verify callback was called for each change
    assert len(callback_calls) == 3
    assert callback_calls[0].value == ConversationExecutionStatus.RUNNING
    assert callback_calls[1].value == ConversationExecutionStatus.PAUSED
    assert callback_calls[2].value == ConversationExecutionStatus.FINISHED


def test_callback_can_be_cleared(state):
    """Test that callback can be cleared by setting to None."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    # Set and then clear the callback
    state.set_on_state_change(callback)
    state.set_on_state_change(None)

    # Change state - callback should not be called
    with state:
        state.execution_status = ConversationExecutionStatus.RUNNING

    # Verify callback was not called
    assert len(callback_calls) == 0


def test_callback_exception_does_not_break_state_change(state):
    """Test that exceptions in callback don't prevent state changes."""

    def bad_callback(event: ConversationStateUpdateEvent):
        raise ValueError("Callback error")

    state.set_on_state_change(bad_callback)

    # Change state - should not raise despite callback error
    with state:
        state.execution_status = ConversationExecutionStatus.RUNNING

    # Verify state was still changed
    assert state.execution_status == ConversationExecutionStatus.RUNNING


def test_callback_not_called_without_lock(state):
    """Test that callback is only called when state is modified within lock."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    state.set_on_state_change(callback)

    # This should still trigger callback since __setattr__ is called
    with state:
        state.execution_status = ConversationExecutionStatus.RUNNING

    # Verify callback was called
    assert len(callback_calls) == 1


def test_callback_with_different_field_types(state):
    """Test callback works with different types of fields."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    state.set_on_state_change(callback)

    # Change different types of fields
    with state:
        state.execution_status = ConversationExecutionStatus.RUNNING
        state.max_iterations = 100
        state.stuck_detection = False

    # Verify callback was called for each change
    assert len(callback_calls) == 3
    assert callback_calls[0].key == "execution_status"
    assert callback_calls[1].key == "max_iterations"
    assert callback_calls[2].key == "stuck_detection"


def test_callback_receives_correct_new_value(state):
    """Test that callback receives the correct new value."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    # Set initial value
    with state:
        state.max_iterations = 50

    # Now set callback and change value again
    state.set_on_state_change(callback)

    with state:
        state.max_iterations = 100

    # Verify new value is correct
    assert len(callback_calls) == 1
    assert callback_calls[0].key == "max_iterations"
    assert callback_calls[0].value == 100
