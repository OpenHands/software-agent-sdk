"""Test for batch atomicity when condensation forgets ObservationEvents.

This test reproduces the bug where condensation forgets an ObservationEvent,
causing its corresponding ActionEvent to be filtered out by filter_unmatched_tool_calls,
but other ActionEvents in the same batch (same llm_response_id) are NOT filtered out.

This breaks the Anthropic API requirement that tool_use blocks must have corresponding
tool_result blocks.

Error message:
"messages.28: `tool_use` ids were found without `tool_result` blocks immediately after:
toolu_01L5zJ74i3tPdZDVGoMzeMHm. Each `tool_use` block must have a corresponding
`tool_result` block in the next message."
"""

from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
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
        thought=[TextContent(text="Test thought")],
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


def test_batch_atomicity_when_observation_forgotten() -> None:
    """Test that if an ObservationEvent is forgotten, all ActionEvents in the same
    batch are also filtered out.

    This reproduces the bug where:
    1. Action1 (batch A) and Action2 (batch A) are in the same batch
    2. Condensation forgets Obs1 (but not Action1, Action2, or Obs2)
    3. Obs1 matches Action1, Obs2 matches Action2
    4. filter_unmatched_tool_calls filters out Action1 (no matching Obs1)
    5. But Action2 is kept (because Obs2 matches Action2)

    This breaks the Anthropic API because Action1 and Action2 were originally
    in the same LLM response, and now Action2 is orphaned without its batch mate.
    """
    # Create a batch of 2 actions from the same LLM response
    llm_response_id = "response_1"

    action1 = create_action_event(llm_response_id, "tool_call_1")
    action2 = create_action_event(llm_response_id, "tool_call_2")

    # Create matching observations
    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")

    # Condensation forgets obs1 (but not action1, action2, or obs2)
    # This simulates what might happen if the condenser uses event indices without
    # considering action-observation pairs
    events = [
        message_event("User message"),
        action1,
        action2,
        obs1,
        obs2,
        Condensation(
            forgotten_event_ids=[obs1.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # After the fix: Both action1 and action2 should be filtered out
    # because they're in the same batch and action1 lost its observation
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]

    # action1 should be filtered out because obs1 was forgotten
    assert action1.id not in action_ids_in_view, (
        "action1 should be filtered out because its observation (obs1) was forgotten"
    )

    # action2 should ALSO be filtered out due to batch atomicity
    # (even though obs2 still exists)
    assert action2.id not in action_ids_in_view, (
        "action2 should be filtered out due to batch atomicity, "
        "because action1 (in the same batch) was filtered out"
    )

    # obs2 should also be filtered out because action2 is gone
    obs_ids_in_view = [e.id for e in view.events if isinstance(e, ObservationEvent)]
    assert obs2.id not in obs_ids_in_view, (
        "obs2 should be filtered out because action2 was filtered out"
    )


def test_batch_atomicity_when_multiple_observations_forgotten() -> None:
    """Test batch atomicity when multiple observations are forgotten."""
    llm_response_id = "response_1"

    action1 = create_action_event(llm_response_id, "tool_call_1")
    action2 = create_action_event(llm_response_id, "tool_call_2")
    action3 = create_action_event(llm_response_id, "tool_call_3")

    obs1 = create_observation_event("tool_call_1")
    obs2 = create_observation_event("tool_call_2")
    obs3 = create_observation_event("tool_call_3")

    # Condensation forgets obs1 and obs2 (but not obs3)
    events = [
        message_event("User message"),
        action1,
        action2,
        action3,
        obs1,
        obs2,
        obs3,
        Condensation(
            forgotten_event_ids=[obs1.id, obs2.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # All actions should be filtered out due to batch atomicity
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action1.id not in action_ids_in_view
    assert action2.id not in action_ids_in_view
    assert action3.id not in action_ids_in_view, (
        "action3 should be filtered out due to batch atomicity"
    )

    # obs3 should also be filtered out because action3 is gone
    obs_ids_in_view = [e.id for e in view.events if isinstance(e, ObservationEvent)]
    assert obs3.id not in obs_ids_in_view


def test_batch_atomicity_different_batches_independent() -> None:
    """Test that batch atomicity only affects events in the same batch."""
    batch1_id = "response_1"
    batch2_id = "response_2"

    # First batch
    action1_1 = create_action_event(batch1_id, "tool_call_1")
    action1_2 = create_action_event(batch1_id, "tool_call_2")
    obs1_1 = create_observation_event("tool_call_1")
    obs1_2 = create_observation_event("tool_call_2")

    # Second batch
    action2_1 = create_action_event(batch2_id, "tool_call_3")
    action2_2 = create_action_event(batch2_id, "tool_call_4")
    obs2_1 = create_observation_event("tool_call_3")
    obs2_2 = create_observation_event("tool_call_4")

    # Condensation forgets obs1_1 (from first batch only)
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
            forgotten_event_ids=[obs1_1.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # First batch should be completely filtered out
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action1_1.id not in action_ids_in_view
    assert action1_2.id not in action_ids_in_view, (
        "action1_2 should be filtered out due to batch atomicity"
    )

    # Second batch should be preserved (different batch)
    assert action2_1.id in action_ids_in_view
    assert action2_2.id in action_ids_in_view


def test_single_action_batch_observation_forgotten() -> None:
    """Test that single-action batches work correctly when observation is forgotten."""
    llm_response_id = "response_1"

    action = create_action_event(llm_response_id, "tool_call_1")
    obs = create_observation_event("tool_call_1")

    # Condensation forgets the observation
    events = [
        message_event("User message"),
        action,
        obs,
        Condensation(
            forgotten_event_ids=[obs.id],
            llm_response_id="condensation_response_1",
        ),
    ]

    view = View.from_events(events)

    # Action should be filtered out because its observation was forgotten
    action_ids_in_view = [e.id for e in view.events if isinstance(e, ActionEvent)]
    assert action.id not in action_ids_in_view
