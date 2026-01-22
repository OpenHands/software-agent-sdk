"""Test for confirmation mode issue with condenser view filtering.

This test reproduces the issue where ActionEvents are incorrectly filtered out
when paired with UserRejectObservation or AgentErrorEvent instead of ObservationEvent.
"""

from unittest.mock import create_autospec

from openhands.sdk.context.view import View
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import Message, TextContent


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def test_filter_unmatched_tool_calls_with_user_reject_observation() -> None:
    """Test that ActionEvent paired with UserRejectObservation is not filtered out.

    This reproduces the confirmation mode issue where user rejection causes
    ActionEvents to be incorrectly filtered out by the condenser.
    """
    # Create a mock ActionEvent with tool_call_id
    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = "call_1"
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"

    # Create a UserRejectObservation that responds to the action
    user_reject_obs = UserRejectObservation(
        action_id="action_1",
        tool_name="TerminalTool",
        tool_call_id="call_1",
        rejection_reason="User rejected the action",
    )

    # Create some other events
    message1 = message_event("First message")
    message2 = message_event("Second message")

    events = [
        message1,
        action_event,
        user_reject_obs,
        message2,
    ]

    # Filter the events
    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # Both the ActionEvent and UserRejectObservation should be kept
    # because they form a matched pair (after the fix)
    assert len(result) == 4
    assert action_event in result
    assert user_reject_obs in result
    assert message1 in result
    assert message2 in result


def test_filter_unmatched_tool_calls_with_agent_error_event() -> None:
    """Test that ActionEvent paired with AgentErrorEvent is not filtered out.

    This tests the case where an agent error occurs during tool execution.
    """
    # Create a mock ActionEvent with tool_call_id
    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = "call_1"
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"

    # Create an AgentErrorEvent that responds to the action
    # After the fix, AgentErrorEvent should have tool_name and tool_call_id fields
    agent_error = AgentErrorEvent(
        error="Tool execution failed",
        tool_name="TerminalTool",
        tool_call_id="call_1",
    )

    # Create some other events
    message1 = message_event("First message")
    message2 = message_event("Second message")

    events = [
        message1,
        action_event,
        agent_error,
        message2,
    ]

    # Filter the events
    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # Both the ActionEvent and AgentErrorEvent should be kept
    # because they form a matched pair (after the fix)
    assert len(result) == 4
    assert action_event in result
    assert agent_error in result
    assert message1 in result
    assert message2 in result


def test_filter_unmatched_tool_calls_mixed_observation_types() -> None:
    """Test filtering with mixed observation types.

    This tests a scenario with normal ObservationEvent, UserRejectObservation,
    and AgentErrorEvent to ensure proper filtering behavior.
    """
    # Create ActionEvents
    action_event_1 = create_autospec(ActionEvent, instance=True)
    action_event_1.tool_call_id = "call_1"
    action_event_1.id = "action_1"
    action_event_1.llm_response_id = "response_1"

    action_event_2 = create_autospec(ActionEvent, instance=True)
    action_event_2.tool_call_id = "call_2"
    action_event_2.id = "action_2"
    action_event_2.llm_response_id = "response_2"

    action_event_3 = create_autospec(ActionEvent, instance=True)
    action_event_3.tool_call_id = "call_3"
    action_event_3.id = "action_3"
    action_event_3.llm_response_id = "response_3"

    # Create different types of observations
    # Normal observation - should work
    observation_event = create_autospec(ObservationEvent, instance=True)
    observation_event.tool_call_id = "call_1"
    observation_event.id = "obs_1"

    # User rejection - should work after fix
    user_reject_obs = UserRejectObservation(
        action_id="action_2",
        tool_name="TerminalTool",
        tool_call_id="call_2",
        rejection_reason="User rejected the action",
    )

    # Agent error - should work after fix (but not before)
    agent_error = AgentErrorEvent(
        error="Tool execution failed",
        tool_name="TerminalTool",
        tool_call_id="call_3",
    )

    events = [
        message_event("Start"),
        action_event_1,
        observation_event,
        action_event_2,
        user_reject_obs,
        action_event_3,
        agent_error,
        message_event("End"),
    ]

    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # After fix: all matched pairs should be kept
    # action_event_1 paired with observation_event
    # action_event_2 paired with user_reject_obs
    # action_event_3 paired with agent_error

    # After the fix, all action events should be kept
    # because all observation types are now recognized
    assert len(result) == 8  # All events kept
    assert action_event_1 in result
    assert observation_event in result
    assert action_event_2 in result  # Fixed!
    assert user_reject_obs in result
    assert action_event_3 in result  # Fixed!
    assert agent_error in result
