"""Tests for thinking block consistency in View.manipulation_indices.

This module tests that manipulation_indices correctly handles the case where
some ActionEvents have thinking blocks and some don't. When thinking is enabled,
the Claude API requires that the final assistant message starts with a thinking
block. This means we need to ensure that if any ActionEvent has thinking blocks,
the last ActionEvent batch must also have thinking blocks.

See: https://github.com/OpenHands/software-agent-sdk/issues/1438
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
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] | None = None,
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
        thought=[TextContent(text="Test thought")],
        thinking_blocks=thinking_blocks or [],
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


def test_thinking_block_consistency_last_batch_without_thinking() -> None:
    """Test that manipulation_indices protects thinking block consistency.

    This test reproduces the bug from issue #1438 where:
    1. Some ActionEvents have thinking blocks (from when thinking was enabled)
    2. Some ActionEvents don't have thinking blocks
    3. After condensation, the LAST ActionEvent batch doesn't have thinking blocks
    4. But the API expects the last assistant message to start with a thinking block

    The fix ensures that cut points are only allowed after batches WITH thinking.
    This way, any valid cut leaves a final batch with thinking blocks.
    """
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # Batch 1: Has thinking blocks
    action1 = create_action_event(
        "response_1", "tool_call_1", thinking_blocks=thinking_blocks
    )
    obs1 = create_observation_event("tool_call_1")

    # Batch 2: No thinking blocks
    action2 = create_action_event("response_2", "tool_call_2")
    obs2 = create_observation_event("tool_call_2")

    # Batch 3: No thinking blocks
    action3 = create_action_event("response_3", "tool_call_3")
    obs3 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        action3,
        obs3,
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Valid cut points:
    # - 0: remove everything (valid - no batches)
    # - 1: keep only message (valid - no batches)
    # - 3: keep batch 1 (valid - batch 1 has thinking)
    #
    # Invalid cut points (would leave non-thinking batch as final):
    # - 5: keep batches 1, 2 (invalid - batch 2 is final, no thinking)
    # - 7: keep batches 1, 2, 3 (invalid - batch 3 is final, no thinking)
    assert indices == [0, 1, 3], (
        f"Expected [0, 1, 3] but got {indices}. "
        "Cut points should only be allowed after batches with thinking blocks."
    )


def test_thinking_block_consistency_all_batches_have_thinking() -> None:
    """Test that when all batches have thinking blocks, they can be manipulated
    independently.
    """
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # All batches have thinking blocks
    action1 = create_action_event(
        "response_1", "tool_call_1", thinking_blocks=thinking_blocks
    )
    obs1 = create_observation_event("tool_call_1")

    action2 = create_action_event(
        "response_2", "tool_call_2", thinking_blocks=thinking_blocks
    )
    obs2 = create_observation_event("tool_call_2")

    action3 = create_action_event(
        "response_3", "tool_call_3", thinking_blocks=thinking_blocks
    )
    obs3 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        action3,
        obs3,
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # When all batches have thinking blocks, they can be manipulated independently
    # [0 msg 1 batch1 3 batch2 5 batch3 7]
    assert indices == [0, 1, 3, 5, 7], (
        f"Expected [0, 1, 3, 5, 7] but got {indices}. "
        "When all batches have thinking blocks, they should be independent."
    )


def test_thinking_block_consistency_no_thinking_blocks() -> None:
    """Test that when no batches have thinking blocks, they can be manipulated
    independently.
    """
    # No batches have thinking blocks
    action1 = create_action_event("response_1", "tool_call_1")
    obs1 = create_observation_event("tool_call_1")

    action2 = create_action_event("response_2", "tool_call_2")
    obs2 = create_observation_event("tool_call_2")

    action3 = create_action_event("response_3", "tool_call_3")
    obs3 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        action3,
        obs3,
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # When no batches have thinking blocks, they can be manipulated independently
    # [0 msg 1 batch1 3 batch2 5 batch3 7]
    assert indices == [0, 1, 3, 5, 7], (
        f"Expected [0, 1, 3, 5, 7] but got {indices}. "
        "When no batches have thinking blocks, they should be independent."
    )


def test_thinking_block_consistency_middle_batch_has_thinking() -> None:
    """Test that when a middle batch has thinking blocks, cut points are
    only allowed after batches with thinking.
    """
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # Batch 1: No thinking blocks
    action1 = create_action_event("response_1", "tool_call_1")
    obs1 = create_observation_event("tool_call_1")

    # Batch 2: Has thinking blocks
    action2 = create_action_event(
        "response_2", "tool_call_2", thinking_blocks=thinking_blocks
    )
    obs2 = create_observation_event("tool_call_2")

    # Batch 3: No thinking blocks
    action3 = create_action_event("response_3", "tool_call_3")
    obs3 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        action3,
        obs3,
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Valid cut points:
    # - 0: remove everything (valid - no batches)
    # - 1: keep only message (valid - no batches)
    # - 5: keep batches 1, 2 (valid - batch 2 has thinking)
    #
    # Invalid cut points:
    # - 3: keep batch 1 (invalid - batch 1 is final, no thinking)
    # - 7: keep batches 1, 2, 3 (invalid - batch 3 is final, no thinking)
    assert indices == [0, 1, 5], (
        f"Expected [0, 1, 5] but got {indices}. "
        "Cut points should only be allowed after batches with thinking blocks."
    )


def test_thinking_block_consistency_last_batch_has_thinking() -> None:
    """Test that when only the last batch has thinking blocks, cut points
    are only allowed after the last batch (which has thinking).
    """
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # Batch 1: No thinking blocks
    action1 = create_action_event("response_1", "tool_call_1")
    obs1 = create_observation_event("tool_call_1")

    # Batch 2: No thinking blocks
    action2 = create_action_event("response_2", "tool_call_2")
    obs2 = create_observation_event("tool_call_2")

    # Batch 3: Has thinking blocks
    action3 = create_action_event(
        "response_3", "tool_call_3", thinking_blocks=thinking_blocks
    )
    obs3 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        action3,
        obs3,
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Valid cut points:
    # - 0: remove everything (valid - no batches)
    # - 1: keep only message (valid - no batches)
    # - 7: keep all batches (valid - batch 3 is final, has thinking)
    #
    # Invalid cut points:
    # - 3: keep batch 1 (invalid - batch 1 is final, no thinking)
    # - 5: keep batches 1, 2 (invalid - batch 2 is final, no thinking)
    assert indices == [0, 1, 7], (
        f"Expected [0, 1, 7] but got {indices}. "
        "Cut points should only be allowed after batches with thinking blocks."
    )


def test_thinking_block_consistency_condensation_removes_thinking_batch() -> None:
    """Test that condensation correctly handles removing a batch with thinking
    blocks when subsequent batches don't have thinking blocks.

    This is the core bug from issue #1438: after condensation, the last batch
    doesn't have thinking blocks, but the API expects it to.
    """
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # Batch 1: Has thinking blocks
    action1 = create_action_event(
        "response_1", "tool_call_1", thinking_blocks=thinking_blocks
    )
    obs1 = create_observation_event("tool_call_1")

    # Batch 2: No thinking blocks
    action2 = create_action_event("response_2", "tool_call_2")
    obs2 = create_observation_event("tool_call_2")

    # Condensation forgets batch 1 (the one with thinking blocks)
    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        Condensation(
            forgotten_event_ids=[action1.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # After the fix: If batch 1 (with thinking) is forgotten, batch 2 (without
    # thinking) should also be forgotten to maintain consistency.
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]

    # Both batches should be forgotten
    assert action1.id not in action_ids_in_view, (
        "action1 should be forgotten (explicitly in forgotten_event_ids)"
    )
    assert action2.id not in action_ids_in_view, (
        "action2 should be forgotten due to thinking block consistency - "
        "if we remove the batch with thinking blocks, we must also remove "
        "subsequent batches without thinking blocks"
    )


def test_thinking_block_consistency_multi_action_batch() -> None:
    """Test thinking block consistency with multi-action batches."""
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # Batch 1: Multi-action batch with thinking blocks (only first action has it)
    action1_1 = create_action_event(
        "response_1", "tool_call_1", thinking_blocks=thinking_blocks
    )
    action1_2 = create_action_event("response_1", "tool_call_2")
    obs1_1 = create_observation_event("tool_call_1")
    obs1_2 = create_observation_event("tool_call_2")

    # Batch 2: Single action without thinking blocks
    action2 = create_action_event("response_2", "tool_call_3")
    obs2 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1_1,
        action1_2,
        obs1_1,
        obs1_2,
        action2,
        obs2,
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    # Valid cut points:
    # - 0: remove everything (valid - no batches)
    # - 1: keep only message (valid - no batches)
    # - 5: keep batch 1 (valid - batch 1 has thinking)
    #
    # Invalid cut points:
    # - 7: keep batches 1, 2 (invalid - batch 2 is final, no thinking)
    assert indices == [0, 1, 5], (
        f"Expected [0, 1, 5] but got {indices}. "
        "Cut points should only be allowed after batches with thinking blocks."
    )
