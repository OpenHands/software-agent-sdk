"""Tests for tool-loop aware manipulation indices.

This module tests that manipulation_indices correctly identifies tool loop
boundaries. A tool loop starts with a batch that has thinking blocks and
continues through all subsequent batches until a non-batch event is encountered.
"""

from openhands.sdk.context.view import View
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    thinking_blocks: list[ThinkingBlock] | None = None,
) -> ActionEvent:
    """Helper to create an ActionEvent."""
    action = MCPToolAction(data={})
    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments="{}",
        origin="completion",
    )

    return ActionEvent(
        thought=[TextContent(text="Test thought")],
        thinking_blocks=list(thinking_blocks) if thinking_blocks else [],  # type: ignore[arg-type]
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
        source="agent",
    )


def create_observation_event(
    tool_call_id: str, content: str = "Success", tool_name: str = "test_tool"
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


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def test_single_batch_with_thinking():
    """Test that a single batch with thinking blocks forms a tool loop."""
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message"),
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 3]
    # - 0: before user message
    # - 1: before tool loop (action + observation)
    # - 3: after tool loop
    assert indices == [0, 1, 3]


def test_tool_loop_multiple_batches():
    """Test that a tool loop continues through multiple consecutive batches."""
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message"),
        # Tool loop starts here with thinking
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        # Continues with second batch (no thinking)
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        # Continues with third batch (no thinking)
        create_action_event("resp_3", "call_3"),
        create_observation_event("call_3"),
        # Tool loop ends when we hit next user message
        message_event("Next user message"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 7, 8]
    # - 0: before first user message
    # - 1: before tool loop (all 3 batches are one atomic unit)
    # - 7: after tool loop, before second user message
    # - 8: after second user message
    assert indices == [0, 1, 7, 8]


def test_tool_loop_ends_at_non_batch_event():
    """Test that a tool loop ends when encountering a non-batch event."""
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message 1"),
        # First tool loop
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        # Non-batch event ends the tool loop
        message_event("User message 2"),
        # Second tool loop starts
        create_action_event("resp_3", "call_3", thinking_blocks=thinking),
        create_observation_event("call_3"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 5, 6, 8]
    # - 0: before first user message
    # - 1: before first tool loop (batches 1-2)
    # - 5: after first tool loop, before second user message
    # - 6: after second user message, before second tool loop
    # - 8: after second tool loop
    assert indices == [0, 1, 5, 6, 8]


def test_batch_without_thinking_not_a_tool_loop():
    """Test that batches without thinking blocks are treated as regular batches."""
    events = [
        message_event("User message"),
        # Regular batch without thinking
        create_action_event("resp_1", "call_1"),
        create_observation_event("call_1"),
        # Another regular batch
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 3, 5]
    # Each batch is separate since no thinking blocks
    # - 0: before user message
    # - 1: before first batch
    # - 3: after first batch, before second batch
    # - 5: after second batch
    assert indices == [0, 1, 3, 5]


def test_multiple_separate_tool_loops():
    """Test multiple tool loops separated by user messages."""
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User 1"),
        # First tool loop
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        message_event("User 2"),
        # Second tool loop
        create_action_event("resp_3", "call_3", thinking_blocks=thinking),
        create_observation_event("call_3"),
        message_event("User 3"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 5, 6, 8, 9]
    # - 0: before user 1
    # - 1: before first tool loop
    # - 5: after first tool loop, before user 2
    # - 6: after user 2, before second tool loop
    # - 8: after second tool loop, before user 3
    # - 9: after user 3
    assert indices == [0, 1, 5, 6, 8, 9]


def test_parallel_tool_calls_in_tool_loop():
    """Test that parallel tool calls within a batch are handled correctly."""
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message"),
        # Tool loop starts with parallel tool calls
        create_action_event("resp_1", "call_1a", thinking_blocks=thinking),
        create_action_event("resp_1", "call_1b"),  # Same response_id = parallel
        create_observation_event("call_1a"),
        create_observation_event("call_1b"),
        # Second batch in the tool loop
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        message_event("Next user message"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 7, 8]
    # - 0: before user message
    # - 1: before tool loop (includes both batches)
    # - 7: after tool loop, before next user message
    # - 8: after next user message
    assert indices == [0, 1, 7, 8]


def test_empty_events():
    """Test manipulation indices with empty events list."""
    view = View.from_events([])
    indices = view.manipulation_indices
    assert indices == [0]


def test_only_user_messages():
    """Test manipulation indices with only user messages (no batches)."""
    events = [
        message_event("User 1"),
        message_event("User 2"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries at each message
    # - 0: before first message
    # - 1: after first message, before second message
    # - 2: after second message
    assert indices == [0, 1, 2]
