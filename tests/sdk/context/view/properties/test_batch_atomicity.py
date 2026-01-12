"""Tests for BatchAtomicityProperty.

This module tests the BatchAtomicityProperty class independently from the View class.
The property ensures that ActionEvents sharing the same llm_response_id form an atomic
unit that cannot be split during condensation.

Note: View-level integration tests for batch atomicity also exist in
tests/sdk/context/view/test_view_batch_atomicity.py. These tests will eventually
be removed once we're satisfied with the property-level tests.
"""

from openhands.sdk.context.view.properties.batch_atomicity import (
    BatchAtomicityProperty,
)
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
        thinking_blocks=thinking_blocks or [],  # type: ignore
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


# ============================================================================
# Tests for enforce() method
# ============================================================================


def test_enforce_no_removal_when_all_actions_present() -> None:
    """Test that no events are removed when all actions in a batch are present."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    current_view = [action1, action2, action3]
    all_events = [action1, action2, action3]

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_removes_partial_batch() -> None:
    """Test that partial batches are completely removed."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    # All events exist
    all_events = [action1, action2, action3]

    # But view only has some of them
    current_view = [action1, action3]  # Missing action2

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Should remove all actions from the partial batch
    assert action1.id in to_remove
    assert action3.id in to_remove


def test_enforce_single_action_batch_not_affected() -> None:
    """Test that single-action batches are not affected by enforcement."""
    action = create_action_event("response_1", "call_1")

    current_view = [action]
    all_events = [action]

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_multiple_batches_only_removes_partial() -> None:
    """Test that only partial batches are removed, not complete ones."""
    # Batch 1: complete in view
    batch1_action1 = create_action_event("response_1", "call_1")
    batch1_action2 = create_action_event("response_1", "call_2")

    # Batch 2: partial in view
    batch2_action1 = create_action_event("response_2", "call_3")
    batch2_action2 = create_action_event("response_2", "call_4")

    all_events = [batch1_action1, batch1_action2, batch2_action1, batch2_action2]

    # View has all of batch1 but only part of batch2
    current_view = [batch1_action1, batch1_action2, batch2_action1]

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Should only remove batch2_action1 (from the partial batch)
    assert batch1_action1.id not in to_remove
    assert batch1_action2.id not in to_remove
    assert batch2_action1.id in to_remove


def test_enforce_with_non_action_events() -> None:
    """Test that non-action events don't interfere with batch atomicity."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    msg = message_event("Test message")

    all_events = [action1, action2, msg]
    current_view = [action1, msg]  # Missing action2

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Should remove action1 from the partial batch
    assert action1.id in to_remove
    # Message should not be affected
    assert msg.id not in to_remove


def test_enforce_empty_view() -> None:
    """Test enforce with empty view."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")

    all_events = [action1, action2]
    current_view = []

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_with_thinking_blocks() -> None:
    """Test that batches with thinking blocks are handled correctly."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Extended thinking", signature="sig1")
    ]

    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    all_events = [action1, action2, action3]
    current_view = [action1, action2]  # Missing action3

    prop = BatchAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Should remove all present actions from partial batch
    assert action1.id in to_remove
    assert action2.id in to_remove


# ============================================================================
# Tests for manipulation_indices() method
# ============================================================================


def test_manipulation_indices_empty_events() -> None:
    """Test manipulation indices with no events."""
    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices([], [])

    # With no events, only index 0 is valid
    assert indices == {0}


def test_manipulation_indices_single_action() -> None:
    """Test manipulation indices with a single action."""
    action = create_action_event("response_1", "call_1")

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices([action], [action])

    # Single action batch allows manipulation before and after
    assert indices == {0, 1}


def test_manipulation_indices_multi_action_batch() -> None:
    """Test manipulation indices with multi-action batch."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    events = [action1, action2, action3]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Can only manipulate at boundaries: before first action and after last
    # Cannot manipulate between action1-action2 or action2-action3
    assert indices == {0, 3}


def test_manipulation_indices_interleaved_batch() -> None:
    """Test manipulation indices with batch actions interleaved with observations."""
    action1 = create_action_event("response_1", "call_1")
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_1", "call_2")
    obs2 = create_observation_event("call_2")

    events = [action1, obs1, action2, obs2]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Batch spans from index 0 (action1) to index 3 (obs2, last observation)
    # The batch includes observations, so range extends to the last observation
    # Can't manipulate at indices 1, 2, 3 (within batch range w/ observations)
    # Can manipulate at 0 (before), 4 (end)
    assert indices == {0, 4}


def test_manipulation_indices_multiple_batches() -> None:
    """Test manipulation indices with multiple separate batches."""
    # Batch 1
    action1_1 = create_action_event("response_1", "call_1")
    action1_2 = create_action_event("response_1", "call_2")

    # Batch 2
    action2_1 = create_action_event("response_2", "call_3")
    action2_2 = create_action_event("response_2", "call_4")

    events = [action1_1, action1_2, action2_1, action2_2]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Batch 1: indices 0-1, Batch 2: indices 2-3
    # Can manipulate at: 0 (before batch1), 2 (between batches), 4 (after batch2)
    assert indices == {0, 2, 4}


def test_manipulation_indices_batches_with_messages() -> None:
    """Test manipulation indices with messages between batches."""
    msg1 = message_event("Start")

    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")

    msg2 = message_event("Middle")

    action3 = create_action_event("response_2", "call_3")

    events = [msg1, action1, action2, msg2, action3]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # msg1 at 0, batch1 at 1-2, msg2 at 3, action3 at 4
    # Can manipulate at: 0, 1, 3, 4, 5
    # Cannot manipulate at: 2 (within batch1)
    assert indices == {0, 1, 3, 4, 5}


def test_manipulation_indices_non_consecutive_batch() -> None:
    """Test manipulation indices when batch actions are non-consecutive."""
    action1 = create_action_event("response_1", "call_1")
    msg = message_event("Between")
    action2 = create_action_event("response_1", "call_2")

    events = [action1, msg, action2]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Batch spans from index 0 to 2, so can't manipulate at 1 or 2
    assert indices == {0, 3}


def test_manipulation_indices_only_single_action_batches() -> None:
    """Test that single-action batches without observations don't restrict indices."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_2", "call_2")
    action3 = create_action_event("response_3", "call_3")

    events = [action1, action2, action3]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # All single-action batches without observations, so can manipulate anywhere
    # Each batch is just a single action with no observation to extend to
    assert indices == {0, 1, 2, 3}


def test_manipulation_indices_complex_scenario() -> None:
    """Test complex scenario with multiple batches and event types."""
    msg1 = message_event("Start")

    # Batch 1: 3 actions
    batch1_a1 = create_action_event("response_1", "call_1")
    batch1_a2 = create_action_event("response_1", "call_2")
    batch1_a3 = create_action_event("response_1", "call_3")

    obs1 = create_observation_event("call_1")

    msg2 = message_event("Middle")

    # Batch 2: 2 actions
    batch2_a1 = create_action_event("response_2", "call_4")
    batch2_a2 = create_action_event("response_2", "call_5")

    events = [msg1, batch1_a1, batch1_a2, batch1_a3, obs1, msg2, batch2_a1, batch2_a2]

    prop = BatchAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # msg1: 0
    # batch1: 1-4 (includes actions 1-3 and obs1 at index 4)
    # msg2: 5
    # batch2: 6-7 (no observations, so just actions)
    # end: 8
    # Can manipulate at: 0, 1 (before batch1), 5 (between batches),
    # 6 (before batch2), 8 (end)
    assert indices == {0, 1, 5, 6, 8}
