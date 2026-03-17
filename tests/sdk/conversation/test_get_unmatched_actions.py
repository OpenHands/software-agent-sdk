"""
Unit tests for get_unmatched_actions method in ConversationState.

Tests the behavior of action matching with various observation types including:
- ObservationEvent
- UserRejectObservation
- AgentErrorEvent (crash recovery scenario)

Related Issue: https://github.com/OpenHands/agent-sdk/issues/2298
"""

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import Function

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.event.base import Event
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.schema import Action, Observation


class MockTestAction(Action):
    """Mock action schema for testing."""

    command: str


class MockTestObservation(Observation):
    """Mock observation schema for testing."""

    result: str

    @property
    def visualize(self):
        from rich.text import Text

        return Text(self.result)


def _create_action_event(
    call_id: str = "call_1",
    command: str = "test_command",
) -> ActionEvent:
    """Helper to create test ActionEvent."""
    action = MockTestAction(command=command)

    litellm_tool_call = ChatCompletionMessageToolCall(
        id=call_id,
        type="function",
        function=Function(
            name="test_tool",
            arguments=f'{{"command": "{command}"}}',
        ),
    )

    tool_call = MessageToolCall.from_chat_tool_call(litellm_tool_call)

    return ActionEvent(
        source="agent",
        thought=[TextContent(text="Test thought")],
        action=action,
        tool_name="test_tool",
        tool_call_id=call_id,
        tool_call=tool_call,
        llm_response_id="response_1",
    )


def test_action_without_observation_is_unmatched():
    """Test that an action without any observation is considered unmatched."""
    action = _create_action_event(call_id="call_1")
    events: list[Event] = [action]

    unmatched = ConversationState.get_unmatched_actions(events)

    assert len(unmatched) == 1
    assert unmatched[0].id == action.id


def test_action_with_observation_event_is_matched():
    """Test that an action with ObservationEvent is matched."""
    action = _create_action_event(call_id="call_1")
    observation = ObservationEvent(
        source="environment",
        observation=MockTestObservation(result="test result"),
        action_id=action.id,
        tool_name="test_tool",
        tool_call_id="call_1",
    )
    events: list[Event] = [action, observation]

    unmatched = ConversationState.get_unmatched_actions(events)

    assert len(unmatched) == 0


def test_action_with_user_reject_observation_is_matched():
    """Test that an action with UserRejectObservation is matched."""
    action = _create_action_event(call_id="call_1")
    rejection = UserRejectObservation(
        action_id=action.id,
        tool_name="test_tool",
        tool_call_id="call_1",
        rejection_reason="User rejected the action",
    )
    events: list[Event] = [action, rejection]

    unmatched = ConversationState.get_unmatched_actions(events)

    assert len(unmatched) == 0


def test_action_with_agent_error_event_is_matched():
    """Test that an action with AgentErrorEvent is matched.

    This is the crash recovery scenario where:
    1. ActionEvent is created (tool_call_id=X)
    2. Server crashes during execution
    3. On restart, crash recovery emits AgentErrorEvent (tool_call_id=X)
    4. The action should now be considered "matched" and NOT be re-executed

    Related issue: https://github.com/OpenHands/agent-sdk/issues/2298
    """
    action = _create_action_event(call_id="call_crash")
    error_event = AgentErrorEvent(
        tool_name="test_tool",
        tool_call_id="call_crash",
        error=(
            "A restart occurred while this tool was in progress. "
            "This may indicate a fatal memory error or system crash."
        ),
    )
    events: list[Event] = [action, error_event]

    unmatched = ConversationState.get_unmatched_actions(events)

    # The action should NOT be in unmatched because AgentErrorEvent was emitted
    assert len(unmatched) == 0


def test_multiple_actions_with_mixed_responses():
    """Test matching with multiple actions and mixed observation types."""
    action1 = _create_action_event(call_id="call_1", command="cmd1")
    action2 = _create_action_event(call_id="call_2", command="cmd2")
    action3 = _create_action_event(call_id="call_3", command="cmd3")
    action4 = _create_action_event(call_id="call_4", command="cmd4")

    # action1 gets ObservationEvent
    obs1 = ObservationEvent(
        source="environment",
        observation=MockTestObservation(result="result1"),
        action_id=action1.id,
        tool_name="test_tool",
        tool_call_id="call_1",
    )

    # action2 gets UserRejectObservation
    reject2 = UserRejectObservation(
        action_id=action2.id,
        tool_name="test_tool",
        tool_call_id="call_2",
        rejection_reason="Rejected",
    )

    # action3 gets AgentErrorEvent (crash recovery)
    error3 = AgentErrorEvent(
        tool_name="test_tool",
        tool_call_id="call_3",
        error="Crash recovery error",
    )

    # action4 has no response - should be unmatched
    events: list[Event] = [action1, action2, action3, action4, obs1, reject2, error3]

    unmatched = ConversationState.get_unmatched_actions(events)

    # Only action4 should be unmatched
    assert len(unmatched) == 1
    assert unmatched[0].tool_call_id == "call_4"


def test_agent_error_event_matching_by_tool_call_id():
    """Test that AgentErrorEvent matches action by tool_call_id, not action_id.

    AgentErrorEvent does not have action_id field (unlike ObservationEvent),
    so matching must use tool_call_id.
    """
    action = _create_action_event(call_id="specific_call_id")

    # AgentErrorEvent with same tool_call_id
    matching_error = AgentErrorEvent(
        tool_name="test_tool",
        tool_call_id="specific_call_id",
        error="Error message",
    )

    events: list[Event] = [action, matching_error]
    unmatched = ConversationState.get_unmatched_actions(events)

    assert len(unmatched) == 0


def test_agent_error_event_different_tool_call_id_does_not_match():
    """Test that AgentErrorEvent with different tool_call_id does not match."""
    action = _create_action_event(call_id="call_A")

    # AgentErrorEvent with different tool_call_id
    non_matching_error = AgentErrorEvent(
        tool_name="test_tool",
        tool_call_id="call_B",  # Different from action's tool_call_id
        error="Error message",
    )

    events: list[Event] = [action, non_matching_error]
    unmatched = ConversationState.get_unmatched_actions(events)

    # Action should still be unmatched as error is for different tool_call_id
    assert len(unmatched) == 1
    assert unmatched[0].tool_call_id == "call_A"


def test_crash_recovery_scenario_prevents_duplicate_execution():
    """Test the full crash recovery scenario described in issue #2298.

    Scenario:
    1. ActionEvent created (tool_call_id=X)
    2. Server crashes during tool execution
    3. On restart, crash recovery emits AgentErrorEvent (tool_call_id=X)
    4. User calls run() again
    5. get_unmatched_actions() should NOT return the action
    6. Therefore, the action is NOT re-executed (no duplicate observation)
    """
    # Step 1: ActionEvent created
    action = _create_action_event(call_id="crash_action_id")

    # Step 3: Crash recovery emits AgentErrorEvent
    crash_error = AgentErrorEvent(
        tool_name="test_tool",
        tool_call_id="crash_action_id",
        error=(
            "A restart occurred while this tool was in progress. "
            "This may indicate a fatal memory error or system crash. "
            "The tool execution was interrupted and did not complete."
        ),
    )

    events: list[Event] = [action, crash_error]

    # Step 5: get_unmatched_actions() should NOT return the action
    unmatched = ConversationState.get_unmatched_actions(events)

    assert len(unmatched) == 0, (
        "Action with AgentErrorEvent should not be returned as unmatched, "
        "otherwise it will be re-executed causing duplicate observations"
    )


def test_non_executable_action_is_not_considered_unmatched():
    """Test that actions with action=None (non-executable) are not unmatched."""
    litellm_tool_call = ChatCompletionMessageToolCall(
        id="call_nonexec",
        type="function",
        function=Function(
            name="test_tool",
            arguments='{"command": "test"}',
        ),
    )
    tool_call = MessageToolCall.from_chat_tool_call(litellm_tool_call)

    # ActionEvent with action=None (non-executable)
    non_executable_action = ActionEvent(
        source="agent",
        thought=[TextContent(text="Test thought")],
        action=None,  # Non-executable
        tool_name="test_tool",
        tool_call_id="call_nonexec",
        tool_call=tool_call,
        llm_response_id="response_1",
    )

    events: list[Event] = [non_executable_action]
    unmatched = ConversationState.get_unmatched_actions(events)

    # Non-executable actions should not appear in unmatched
    assert len(unmatched) == 0
