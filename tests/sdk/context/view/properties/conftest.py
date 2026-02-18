"""Common fixtures and utilities for view properties tests."""

from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
    event_id: str,
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
) -> ActionEvent:
    """Helper to create an ActionEvent with specified IDs."""
    action = MCPToolAction(data={})

    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments="{}",
        origin="completion",
    )

    return ActionEvent(
        id=event_id,
        thought=[TextContent(text="Test thought")],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
        source="agent",
    )


def create_observation_event(
    event_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    content: str = "Success",
) -> ObservationEvent:
    """Helper to create an ObservationEvent."""
    observation = MCPToolObservation.from_text(
        text=content,
        tool_name=tool_name,
    )
    return ObservationEvent(
        id=event_id,
        observation=observation,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id="action_event_id",
        source="environment",
    )


def create_message_event(event_id: str, content: str) -> MessageEvent:
    """Helper to create a non-tool-loop event (MessageEvent)."""
    return MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def create_action_event_with_none_action(
    event_id: str,
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "missing_tool",
) -> ActionEvent:
    """Helper to create an ActionEvent with action=None (action not executed).

    This is used to test the case where an action was not executed (e.g., tool
    was not found) but still has a matching observation (e.g., AgentErrorEvent).
    """
    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments="{}",
        origin="completion",
    )

    return ActionEvent(
        id=event_id,
        thought=[TextContent(text="Test thought")],
        action=None,  # Action was not executed
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
        source="agent",
    )
