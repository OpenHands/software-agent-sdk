"""Tests for batch atomicity in View.from_events().

This module tests that multi-action batches (multiple ActionEvents from the same
LLM response) are treated atomically during condensation. This is critical for
extended thinking models like Claude Sonnet 4.5, where thinking blocks must stay
with their associated tool calls.
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


def test_batch_atomicity_partial_batch_forgotten() -> None:
    """Test that if one event in a batch is forgotten, all events in that batch are
    forgotten.

    This simulates the scenario where the condenser forgets E44-E46 from a batch
    of E44-E47, leaving only E47. The batch atomicity logic should ensure that
    E47 is also forgotten to prevent thinking blocks from being separated.
    """
    # Create a batch of 4 actions from the same LLM response
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]
    llm_response_id = "response_1"

    action1 = create_action_event(
        llm_response_id, "tool_call_1", thinking_blocks=thinking_blocks
    )
    action2 = create_action_event(llm_response_id, "tool_call_2")
    action3 = create_action_event(llm_response_id, "tool_call_3")
    action4 = create_action_event(llm_response_id, "tool_call_4")

    # Create matching observations
    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")
    obs3 = create_observation_event("tool_call_3")
    obs4 = create_observation_event("tool_call_4")

    # Condensation forgets the first 3 actions (E44-E46), but not the 4th (E47)
    # This simulates what might happen if the condenser uses event indices without
    # considering batch boundaries
    events = [
        message_event("User message"),
        action1,
        action2,
        action3,
        action4,
        obs1,
        obs2,
        obs3,
        obs4,
        Condensation(
            forgotten_event_ids=[action1.id, action2.id, action3.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # Batch atomicity should ensure that action4 is also forgotten
    # even though it wasn't explicitly listed in forgotten_event_ids
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]

    assert action1.id not in action_ids_in_view
    assert action2.id not in action_ids_in_view
    assert action3.id not in action_ids_in_view
    assert action4.id not in action_ids_in_view, (
        "action4 should be forgotten due to batch atomicity, "
        "even though it wasn't explicitly in forgotten_event_ids"
    )

    # Verify observations are also filtered out due to unmatched tool calls
    obs_ids_in_view = [e.id for e in view.events if isinstance(e, ObservationEvent)]
    assert len(obs_ids_in_view) == 0


def test_batch_atomicity_complete_batch_forgotten() -> None:
    """Test that when all events in a batch are forgotten, they're all removed."""
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]
    llm_response_id = "response_1"

    action1 = create_action_event(
        llm_response_id, "tool_call_1", thinking_blocks=thinking_blocks
    )
    action2 = create_action_event(
        llm_response_id, "tool_call_2", thinking_blocks=thinking_blocks
    )

    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")

    events = [
        message_event("User message"),
        action1,
        action2,
        obs1,
        obs2,
        Condensation(
            forgotten_event_ids=[action1.id, action2.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # Both actions should be forgotten
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert len(action_ids_in_view) == 0

    # Observations should also be filtered out
    obs_ids_in_view = [e.id for e in view.events if isinstance(e, ObservationEvent)]
    assert len(obs_ids_in_view) == 0


def test_batch_atomicity_no_forgetting_preserves_batch() -> None:
    """Test that when no events in a batch are forgotten, all are preserved."""
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]
    llm_response_id = "response_1"

    action1 = create_action_event(
        llm_response_id, "tool_call_1", thinking_blocks=thinking_blocks
    )
    action2 = create_action_event(
        llm_response_id, "tool_call_2", thinking_blocks=thinking_blocks
    )
    action3 = create_action_event(
        llm_response_id, "tool_call_3", thinking_blocks=thinking_blocks
    )

    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")
    obs3 = create_observation_event("tool_call_3")

    events = [
        message_event("User message"),
        action1,
        action2,
        action3,
        obs1,
        obs2,
        obs3,
        Condensation(
            forgotten_event_ids=[], llm_response_id="condensation_response_1"
        ),  # Don't forget anything
    ]

    view = View.from_events(events)

    # All actions should be preserved
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action1.id in action_ids_in_view
    assert action2.id in action_ids_in_view
    assert action3.id in action_ids_in_view


def test_batch_atomicity_multiple_batches() -> None:
    """Test that batch atomicity works correctly with multiple separate batches."""
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]

    # First batch
    batch1_id = "response_1"
    action1_1 = create_action_event(
        batch1_id, "tool_call_1", thinking_blocks=thinking_blocks
    )
    action1_2 = create_action_event(
        batch1_id, "tool_call_2", thinking_blocks=thinking_blocks
    )
    obs1_1 = create_observation_event("tool_call_1")
    obs1_2 = create_observation_event("tool_call_2")

    # Second batch
    batch2_id = "response_2"
    action2_1 = create_action_event(
        batch2_id, "tool_call_3", thinking_blocks=thinking_blocks
    )
    action2_2 = create_action_event(
        batch2_id, "tool_call_4", thinking_blocks=thinking_blocks
    )
    obs2_1 = create_observation_event("tool_call_3")
    obs2_2 = create_observation_event("tool_call_4")

    # Forget only the first action of the first batch
    # This should cause the entire first batch to be forgotten, but not the second batch
    events = [
        message_event("User message"),
        action1_1,
        action1_2,
        obs1_1,
        obs1_2,
        message_event("Another message"),
        action2_1,
        action2_2,
        obs2_1,
        obs2_2,
        Condensation(
            forgotten_event_ids=[action1_1.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # First batch should be completely forgotten
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action1_1.id not in action_ids_in_view
    assert action1_2.id not in action_ids_in_view, (
        "action1_2 should be forgotten due to batch atomicity"
    )

    # Second batch should be preserved
    assert action2_1.id in action_ids_in_view
    assert action2_2.id in action_ids_in_view


def test_batch_atomicity_single_action_batch() -> None:
    """Test that batches with a single action work correctly."""
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] = [
        ThinkingBlock(
            type="thinking", thinking="Extended thinking...", signature="sig1"
        )
    ]
    llm_response_id = "response_1"

    action = create_action_event(
        llm_response_id, "tool_call_1", thinking_blocks=thinking_blocks
    )
    obs = create_observation_event("tool_call_1")

    events = [
        message_event("User message"),
        action,
        obs,
        Condensation(
            forgotten_event_ids=[action.id], llm_response_id="condensation_response_1"
        ),
    ]

    view = View.from_events(events)

    # Single action should be forgotten
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action.id not in action_ids_in_view


def test_batch_atomicity_no_thinking_blocks() -> None:
    """Test that batch atomicity works even without thinking blocks.

    While the motivation for batch atomicity is to preserve thinking blocks,
    the logic should work for all multi-action batches.
    """
    llm_response_id = "response_1"

    action1 = create_action_event(llm_response_id, "tool_call_1")
    action2 = create_action_event(llm_response_id, "tool_call_2")
    action3 = create_action_event(llm_response_id, "tool_call_3")

    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")
    obs3 = create_observation_event("tool_call_3")

    # Forget first two actions
    events = [
        message_event("User message"),
        action1,
        obs1,
        action2,
        obs2,
        action3,
        obs3,
        Condensation(
            forgotten_event_ids=[action1.id, action2.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # All actions in the batch should be forgotten due to atomicity
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action1.id not in action_ids_in_view
    assert action2.id not in action_ids_in_view
    assert action3.id not in action_ids_in_view, (
        "action3 should be forgotten due to batch atomicity"
    )
