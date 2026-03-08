"""Common fixtures and utilities for view tests.

This module consolidates common event creation helpers used across the view tests.
"""

from collections.abc import Sequence

from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def create_action_event(
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    thinking_blocks: Sequence[ThinkingBlock | RedactedThinkingBlock] | None = None,
    thinking: str | None = None,
) -> ActionEvent:
    """Helper to create an ActionEvent with specified IDs."""
    action = MCPToolAction(data={})

    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments="{}",
        origin="completion",
    )

    resolved_blocks: list[ThinkingBlock | RedactedThinkingBlock] = []
    if thinking_blocks:
        resolved_blocks = list(thinking_blocks)
    elif thinking is not None:
        resolved_blocks = [ThinkingBlock(thinking=thinking)]

    return ActionEvent(
        thought=[TextContent(text="Test thought")],
        thinking_blocks=resolved_blocks,
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
        source="agent",
    )


def create_observation_event(
    tool_call_id: str,
    content: str = "Success",
    tool_name: str = "test_tool",
) -> ObservationEvent:
    """Helper to create an ObservationEvent."""
    observation = MCPToolObservation.from_text(
        text=content,
        tool_name=tool_name,
    )
    return ObservationEvent(
        observation=observation,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id="action_event_id",
        source="environment",
    )
