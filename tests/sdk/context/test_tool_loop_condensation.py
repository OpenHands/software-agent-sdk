"""Tests for condensation behavior with tool loops.

This module tests how condensation interacts with tool-loop aware manipulation
indices, particularly when there's a single long tool loop that needs to be
condensed.
"""

from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
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


def test_single_long_tool_loop_condensation():
    """Test that a single long tool loop can be fully condensed.

    When condensation needs to happen and the entire view consists of a single
    tool loop, the expectation is that the entire tool loop must be condensed
    as one atomic unit.
    """
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message"),
        # Single long tool loop with 10 batches
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        create_action_event("resp_3", "call_3"),
        create_observation_event("call_3"),
        create_action_event("resp_4", "call_4"),
        create_observation_event("call_4"),
        create_action_event("resp_5", "call_5"),
        create_observation_event("call_5"),
        create_action_event("resp_6", "call_6"),
        create_observation_event("call_6"),
        create_action_event("resp_7", "call_7"),
        create_observation_event("call_7"),
        create_action_event("resp_8", "call_8"),
        create_observation_event("call_8"),
        create_action_event("resp_9", "call_9"),
        create_observation_event("call_9"),
        create_action_event("resp_10", "call_10"),
        create_observation_event("call_10"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 21]
    # - 0: before user message
    # - 1: before tool loop (entire loop is atomic)
    # - 21: after tool loop
    assert indices == [0, 1, 21], f"Expected [0, 1, 21], got {indices}"

    # Now test what happens if we try to condense the middle
    # We should only be able to condense from index 1 to 21 (the entire tool loop)

    # Try to condense using find_next_manipulation_index
    # If we want to keep first 2 events and condense the rest
    forgetting_start = view.find_next_manipulation_index(1, strict=True)
    assert forgetting_start == 21, f"Expected forgetting_start=21, got {forgetting_start}"

    # This means we can't actually condense the middle of this tool loop
    # The only valid condensation would be to remove the entire tool loop


def test_tool_loop_with_prefix_and_suffix():
    """Test condensation with a tool loop that has non-loop events before and after.

    When there are events before and after a tool loop, condensation can work
    by keeping the prefix and suffix, removing the tool loop in the middle.
    """
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User 1"),
        # Some regular batches before the tool loop
        create_action_event("resp_0", "call_0"),
        create_observation_event("call_0"),
        # Tool loop starts
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        create_action_event("resp_3", "call_3"),
        create_observation_event("call_3"),
        # Tool loop ends
        message_event("User 2"),
        # Some events after
        create_action_event("resp_4", "call_4"),
        create_observation_event("call_4"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 3, 9, 10, 12]
    # - 0: before user 1
    # - 1: before first regular batch
    # - 3: after first batch, before tool loop
    # - 9: after tool loop, before user 2
    # - 10: after user 2, before last batch
    # - 12: after last batch
    assert indices == [0, 1, 3, 9, 10, 12], f"Expected [0, 1, 3, 9, 10, 12], got {indices}"

    # We can condense the tool loop by removing events [3:9]
    # This is the entire tool loop as one atomic unit
    forgotten_ids = [view.events[i].id for i in range(3, 9)]
    condensed_events = list(events) + [
        Condensation(
            forgotten_event_ids=forgotten_ids,
            llm_response_id="condensation_test",
        )
    ]

    condensed_view = View.from_events(condensed_events)

    # After condensation, we should have:
    # - User 1
    # - Regular batch (resp_0)
    # - User 2
    # - Regular batch (resp_4)
    assert len(condensed_view.events) == 6  # 2 messages + 2 actions + 2 observations


def test_multiple_tool_loops_condensation():
    """Test that we can condense one tool loop while keeping another."""
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
        create_action_event("resp_4", "call_4"),
        create_observation_event("call_4"),
        message_event("User 3"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Should have boundaries: [0, 1, 5, 6, 10, 11]
    # - 0: before user 1
    # - 1: before first tool loop
    # - 5: after first tool loop, before user 2
    # - 6: after user 2, before second tool loop
    # - 10: after second tool loop, before user 3
    # - 11: after user 3
    assert indices == [0, 1, 5, 6, 10, 11], f"Expected [0, 1, 5, 6, 10, 11], got {indices}"

    # We can condense the first tool loop
    forgotten_ids = [view.events[i].id for i in range(1, 5)]
    condensed_events = list(events) + [
        Condensation(
            forgotten_event_ids=forgotten_ids,
            llm_response_id="condensation_test",
        )
    ]

    condensed_view = View.from_events(condensed_events)

    # After condensation, we should have:
    # - User 1, User 2
    # - Second tool loop (4 events)
    # - User 3
    assert len(condensed_view.events) == 7  # 3 messages + 2 actions + 2 observations


def test_cannot_partially_condense_tool_loop():
    """Test that we cannot condense only part of a tool loop.

    The manipulation indices should prevent splitting a tool loop, so any
    condensation attempt should either take the whole tool loop or none of it.
    """
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message"),
        # Tool loop with 3 batches
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        create_action_event("resp_3", "call_3"),
        create_observation_event("call_3"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # The tool loop is atomic: [0, 1, 7]
    assert indices == [0, 1, 7]

    # Attempting to use find_next_manipulation_index to split in the middle
    # should skip to the end of the tool loop

    # If we want to keep first 3 events (user + first batch)
    next_index = view.find_next_manipulation_index(3, strict=True)
    # Should jump to 7 (end of tool loop), not 3 or 5
    assert next_index == 7, f"Expected 7, got {next_index}"

    # If we want to start forgetting at event 2
    next_index = view.find_next_manipulation_index(2, strict=False)
    # Should stay at 7 (can't split the tool loop)
    assert next_index == 7, f"Expected 7, got {next_index}"
