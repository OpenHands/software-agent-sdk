"""Tests for View.manipulation_indices property.

This module tests the property that identifies safe indices for manipulating
events (inserting new events or forgetting ranges) while respecting atomicity
constraints.
"""

from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import (
    ThinkingBlock,
)
from tests.sdk.context.view.conftest import (  # noqa: F401
    create_action_event,
    create_observation_event,
    message_event,
)


def test_empty_list() -> None:
    """Test manipulation_indices with empty event list."""
    view = View.from_events([])
    assert view.manipulation_indices == {0}


def test_single_message_event() -> None:
    """Test manipulation_indices with a single message event."""
    events = [message_event("Event 0")]
    view = View.from_events(events)

    # Should have boundaries before and after the single message
    assert view.manipulation_indices == {0, 1}


def test_multiple_message_events() -> None:
    """Test manipulation_indices with multiple message events."""
    events = [
        message_event("Event 0"),
        message_event("Event 1"),
        message_event("Event 2"),
    ]
    view = View.from_events(events)

    # Each message is its own atomic unit, so boundaries exist between all of them
    assert view.manipulation_indices == {0, 1, 2, 3}


def test_single_action_observation_pair() -> None:
    """Test manipulation_indices with a single action-observation pair."""
    action = create_action_event("response_1", "tool_call_1")
    obs = create_observation_event("tool_call_1")

    events = [action, obs]
    indices = View.from_events(events).manipulation_indices

    # The pair is an atomic unit, so boundaries are only at start and end
    assert indices == {0, 2}


def test_action_observation_with_message_events() -> None:
    """Test manipulation indices with message events around action-observation."""
    msg1 = message_event("Before")
    action = create_action_event("response_1", "tool_call_1")
    obs = create_observation_event("tool_call_1")
    msg2 = message_event("After")

    events = [msg1, action, obs, msg2]
    indices = View.from_events(events).manipulation_indices

    # Boundaries: [0 msg1 1 (action+obs) 3 msg2 4]
    assert indices == {0, 1, 3, 4}


def test_batch_of_actions_simple() -> None:
    """Test manipulation indices with a batch of actions from same LLM response."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action1 = create_action_event("response_1", "tool_call_1", thinking_blocks=thinking)
    action2 = create_action_event("response_1", "tool_call_2")
    action3 = create_action_event("response_1", "tool_call_3")

    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")
    obs3 = create_observation_event("tool_call_3")

    events = [action1, action2, action3, obs1, obs2, obs3]
    indices = View.from_events(events).manipulation_indices

    # All actions are part of the same batch, and observations extend the range
    # The entire batch (actions + observations) is one atomic unit
    assert indices == {0, 6}


def test_multiple_separate_batches() -> None:
    """Test manipulation indices with multiple separate action batches."""
    # First batch
    action1_1 = create_action_event("response_1", "tool_call_1")
    action1_2 = create_action_event("response_1", "tool_call_2")
    obs1_1 = create_observation_event("tool_call_1")
    obs1_2 = create_observation_event("tool_call_2")

    # Second batch
    action2_1 = create_action_event("response_2", "tool_call_3")
    action2_2 = create_action_event("response_2", "tool_call_4")
    obs2_1 = create_observation_event("tool_call_3")
    obs2_2 = create_observation_event("tool_call_4")

    events = [
        action1_1,
        action1_2,
        obs1_1,
        obs1_2,
        action2_1,
        action2_2,
        obs2_1,
        obs2_2,
    ]
    indices = View.from_events(events).manipulation_indices

    # Two atomic units: batch1 (indices 0-3) and batch2 (indices 4-7)
    assert indices == {0, 4, 8}


def test_batches_separated_by_messages() -> None:
    """Test manipulation indices with messages between action batches."""
    msg1 = message_event("Start")

    action1 = create_action_event("response_1", "tool_call_1")
    action2 = create_action_event("response_1", "tool_call_2")
    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")

    msg2 = message_event("Middle")

    action3 = create_action_event("response_2", "tool_call_3")
    obs3 = create_observation_event("tool_call_3")

    msg3 = message_event("End")

    events = [msg1, action1, action2, obs1, obs2, msg2, action3, obs3, msg3]
    indices = View.from_events(events).manipulation_indices

    # [0 msg1 1 (batch1: action1,action2,obs1,obs2) 5 msg2 6 (batch2) 8 msg3 9]
    assert indices == {0, 1, 5, 6, 8, 9}


def test_single_action_in_batch() -> None:
    """Test manipulation indices with a batch containing only one action."""
    action = create_action_event("response_1", "tool_call_1")
    obs = create_observation_event("tool_call_1")

    events = [action, obs]
    indices = View.from_events(events).manipulation_indices

    # Single-action batch is still an atomic unit
    assert indices == {0, 2}


def test_complex_interleaved_scenario() -> None:
    """Test complex scenario with multiple event types interleaved."""
    msg1 = message_event("Message 1")

    # Batch 1: 2 actions
    action1_1 = create_action_event("response_1", "call_1")
    action1_2 = create_action_event("response_1", "call_2")
    obs1_1 = create_observation_event("call_1")

    msg2 = message_event("Message 2")

    obs1_2 = create_observation_event("call_2")  # Observation comes late

    msg3 = message_event("Message 3")

    # Batch 2: 1 action
    action2 = create_action_event("response_2", "call_3")
    obs2 = create_observation_event("call_3")

    events = [
        msg1,
        action1_1,
        action1_2,
        obs1_1,
        msg2,
        obs1_2,
        msg3,
        action2,
        obs2,
    ]
    indices = View.from_events(events).manipulation_indices

    # msg1: [0, 1]
    # batch1 (action1_1, action1_2, obs1_1, msg2, obs1_2): [1, 6]
    # msg3: [6, 7]
    # batch2 (action2, obs2): [7, 9]
    #
    # Wait - msg2 is in between the batch, but it's its own atomic unit
    # Actually, batch1 spans indices 1-5 (action1_1, action1_2, obs1_1, -, obs1_2)
    # But there's a message at index 4
    #
    # Let's recalculate:
    # 0: msg1 (atomic unit)
    # 1: action1_1 (part of batch1)
    # 2: action1_2 (part of batch1)
    # 3: obs1_1 (part of batch1)
    # 4: msg2 (atomic unit but check if it's in batch range)
    # 5: obs1_2 (part of batch1, extends range)
    # 6: msg3 (atomic unit)
    # 7: action2 (part of batch2)
    # 8: obs2 (part of batch2)
    #
    # batch1 range: min(1,2)=1, max after observations: max(2, 5)=5
    # But msg2 at index 4 is between 1 and 5
    #
    # Expected: [0, 1, 6, 7, 9]
    # - 0: before msg1
    # - 1: after msg1, before batch1
    # - 6: after batch1 (which includes indices 1-5), before msg3
    # - 7: after msg3, before batch2
    # - 9: after batch2

    assert indices == {0, 1, 6, 7, 9}


def test_observations_extend_batch_range() -> None:
    """Test that observations extend the atomic unit range of a batch."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")

    msg = message_event("Middle")

    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")

    events = [action1, action2, msg, obs1, obs2]
    indices = View.from_events(events).manipulation_indices

    # Batch includes actions 0-1 and observations 3-4
    # Message at 2 falls within the batch range, so treated as part of it
    # Range: min=0, max=4
    assert indices == {0, 5}


def test_batch_with_all_observations() -> None:
    """Test batch boundaries when all actions have matching observations.

    Note: In practice, from_events() filters out unmatched actions, so this
    tests the realistic scenario where all actions in a batch have observations.
    """
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")

    events = [action1, action2, obs1, obs2]
    view = View.from_events(events)
    indices = view.manipulation_indices

    # The batch is one atomic unit containing both action-observation pairs
    assert indices == {0, 4}


def test_interleaved_batches_and_messages() -> None:
    """Test alternating pattern of batches and messages."""
    msg1 = message_event("Msg 1")

    action1 = create_action_event("response_1", "call_1")
    obs1 = create_observation_event("call_1")

    msg2 = message_event("Msg 2")

    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    msg3 = message_event("Msg 3")

    events = [msg1, action1, obs1, msg2, action2, obs2, msg3]
    indices = View.from_events(events).manipulation_indices

    # [0 msg1 1 batch1 3 msg2 4 batch2 6 msg3 7]
    assert indices == {0, 1, 3, 4, 6, 7}


def test_three_action_batch() -> None:
    """Test batch with three parallel actions."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")
    obs3 = create_observation_event("call_3")

    events = [action1, action2, action3, obs1, obs2, obs3]
    indices = View.from_events(events).manipulation_indices

    # All part of one batch
    assert indices == {0, 6}


def test_consecutive_atomic_units() -> None:
    """Test that consecutive indices correctly define atomic units."""
    msg1 = message_event("Msg 1")
    msg2 = message_event("Msg 2")

    action = create_action_event("response_1", "call_1")
    obs = create_observation_event("call_1")

    msg3 = message_event("Msg 3")

    events = [msg1, msg2, action, obs, msg3]
    indices = View.from_events(events).manipulation_indices

    # [0 msg1 1 msg2 2 batch 4 msg3 5]
    assert indices == {0, 1, 2, 4, 5}

    # Verify atomic units:
    # events[0:1] = [msg1]
    # events[1:2] = [msg2]
    # events[2:4] = [action, obs]
    # events[4:5] = [msg3]


def test_forgetting_range_selection() -> None:
    """Test that ranges between consecutive indices can be safely forgotten."""
    msg1 = message_event("Keep")

    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")

    msg2 = message_event("Keep")

    events = [msg1, action1, action2, obs1, obs2, msg2]
    indices = View.from_events(events).manipulation_indices

    # [0 msg1 1 batch 5 msg2 6]
    assert indices == {0, 1, 5, 6}


def test_add_event_invalidates_cached_indices() -> None:
    """Test that manipulation_indices are recomputed after add_event."""
    view = View()
    view.add_event(message_event("Event 0"))
    indices_after_first = view.manipulation_indices
    assert indices_after_first == {0, 1}

    view.add_event(message_event("Event 1"))
    indices_after_second = view.manipulation_indices
    assert indices_after_second == {0, 1, 2}
    assert indices_after_second is not indices_after_first


def test_add_event_condensation_invalidates_cached_indices() -> None:
    """Test that manipulation_indices are recomputed after a Condensation."""
    events: list[Event] = [message_event(f"Event {i}") for i in range(3)]
    view = View.from_events(events)
    assert view.manipulation_indices == {0, 1, 2, 3}

    condensation = Condensation(
        forgotten_event_ids=[events[0].id],
        llm_response_id="condensation_response_1",
    )
    view.add_event(condensation)
    assert view.manipulation_indices == {0, 1, 2}


def test_enforce_properties_invalidates_cached_indices() -> None:
    """Test that manipulation_indices are recomputed after enforce_properties."""
    # Create a view with messages and an unmatched action (no observation).
    # enforce_properties will drop the unmatched action, invalidating the cache.
    msg1 = message_event("Message 1")
    msg2 = message_event("Message 2")
    action = create_action_event("response_1", "tool_call_1")

    view = View()
    view.add_event(msg1)
    view.add_event(msg2)
    view.add_event(action)

    # Cache the indices before enforcement
    indices_before = view.manipulation_indices
    assert len(view.events) == 3

    # enforce_properties should drop the unmatched action and invalidate cache
    all_events: list[Event] = [msg1, msg2, action]
    view.enforce_properties(all_events)

    assert len(view.events) == 2
    indices_after = view.manipulation_indices
    # The cache was invalidated and a new object was computed
    assert indices_after is not indices_before
    # The indices correctly reflect the post-enforcement state (2 messages)
    assert indices_after == {0, 1, 2}
