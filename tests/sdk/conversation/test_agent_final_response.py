"""Tests for the get_agent_final_response utility function."""

from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction


def test_get_agent_final_response_with_finish_action():
    """Test extracting final response from a finish action."""
    # Create a finish action event
    finish_action = FinishAction(message="Task completed successfully!")
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Finishing the task")],
        action=finish_action,
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    events = [action_event]
    result = get_agent_final_response(events)

    assert result == "Task completed successfully!"


def test_get_agent_final_response_with_message_event():
    """Test extracting final response from a message event."""
    # Create a message event
    message_event = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Here is my response")]
        ),
    )

    events = [message_event]
    result = get_agent_final_response(events)

    assert result == "Here is my response"


def test_get_agent_final_response_with_multiple_events():
    """Test extracting final response when there are multiple events."""
    # Create multiple events - the last agent event should be returned
    user_message = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="Hello")]),
    )

    agent_message1 = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="First response")]
        ),
    )

    agent_message2 = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Final response")]
        ),
    )

    events = [user_message, agent_message1, agent_message2]
    result = get_agent_final_response(events)

    # Should return the last agent message
    assert result == "Final response"


def test_get_agent_final_response_finish_action_takes_precedence():
    """Test that finish action takes precedence over message events."""
    # Create a message event
    agent_message = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Regular message")]
        ),
    )

    # Create a finish action that comes after
    finish_action = FinishAction(message="Finished!")
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Done")],
        action=finish_action,
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    events = [agent_message, action_event]
    result = get_agent_final_response(events)

    # Should return the finish action message (comes last)
    assert result == "Finished!"


def test_get_agent_final_response_empty_events():
    """Test handling of empty events list."""
    events = []
    result = get_agent_final_response(events)

    assert result == ""


def test_get_agent_final_response_no_agent_events():
    """Test handling when there are no agent events."""
    # Create only user events
    user_message = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="Hello")]),
    )

    events = [user_message]
    result = get_agent_final_response(events)

    assert result == ""


def test_get_agent_final_response_with_none_action():
    """Test handling of finish tool call with None action."""
    # Create an action event with tool_name="finish" but action=None
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Trying to finish")],
        action=None,  # No executable action
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    events = [action_event]
    result = get_agent_final_response(events)

    # Should return empty string when action is None
    assert result == ""


def test_get_agent_final_response_with_multiple_content_parts():
    """Test extracting final response with multiple content parts."""
    # Create a message event with multiple text content parts
    message_event = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[
                TextContent(text="Part 1. "),
                TextContent(text="Part 2. "),
                TextContent(text="Part 3."),
            ],
        ),
    )

    events = [message_event]
    result = get_agent_final_response(events)

    assert result == "Part 1. Part 2. Part 3."


def test_get_agent_final_response_ignores_non_agent_finish():
    """Test that finish actions from non-agent sources are ignored."""
    # Create a finish action from user (shouldn't happen but test edge case)
    finish_action = FinishAction(message="User finish")
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="user",  # Not from agent
        thought=[TextContent(text="User thought")],
        action=finish_action,
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    # Also add a regular agent message
    agent_message = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Agent response")]
        ),
    )

    events = [action_event, agent_message]
    result = get_agent_final_response(events)

    # Should return the agent message, not the user finish action
    assert result == "Agent response"


def test_get_agent_final_response_with_non_finish_action():
    """Test that non-finish actions are ignored."""
    # Create a non-finish action event (e.g., read_file)
    tool_call = MessageToolCall(
        id="test-call-id", name="read_file", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Reading file")],
        action=None,
        tool_name="read_file",  # Not a finish action
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    # Also add an agent message
    agent_message = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="File contents")]
        ),
    )

    events = [action_event, agent_message]
    result = get_agent_final_response(events)

    # Should return the agent message
    assert result == "File contents"
