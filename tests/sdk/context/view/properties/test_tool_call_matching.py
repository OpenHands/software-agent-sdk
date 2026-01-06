"""Tests for ToolCallMatchingProperty.

This module tests the ToolCallMatchingProperty class independently from the View class.
The property ensures that ActionEvents and ObservationEvents are properly paired via
tool_call_id. Orphaned actions or observations cause API errors and must be removed.
"""

from openhands.sdk.context.view.properties.tool_call_matching import (
    ToolCallMatchingProperty,
)
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    TextContent,
)
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
        thinking_blocks=[],
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


def create_user_reject_observation(
    tool_call_id: str, tool_name: str = "test_tool"
) -> UserRejectObservation:
    """Helper to create a UserRejectObservation."""
    return UserRejectObservation(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id="action_event_id",
        rejection_reason="User rejected",
        source="environment",
    )


def create_agent_error_event(
    tool_call_id: str, tool_name: str = "test_tool"
) -> AgentErrorEvent:
    """Helper to create an AgentErrorEvent."""
    return AgentErrorEvent(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        error="Test error",
        source="agent",
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


def test_enforce_matched_pairs_no_removal() -> None:
    """Test that matched action-observation pairs are not removed."""
    action = create_action_event("response_1", "call_1")
    obs = create_observation_event("call_1")

    current_view = [action, obs]
    all_events = [action, obs]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_removes_orphaned_action() -> None:
    """Test that actions without matching observations are removed."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    obs1 = create_observation_event("call_1")

    current_view = [action1, action2, obs1]
    all_events = [action1, action2, obs1]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    # action2 has no matching observation
    assert action2.id in to_remove
    assert action1.id not in to_remove
    assert obs1.id not in to_remove


def test_enforce_removes_orphaned_observation() -> None:
    """Test that observations without matching actions are removed."""
    action = create_action_event("response_1", "call_1")
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")  # No matching action

    current_view = [action, obs1, obs2]
    all_events = [action, obs1, obs2]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    # obs2 has no matching action
    assert obs2.id in to_remove
    assert action.id not in to_remove
    assert obs1.id not in to_remove


def test_enforce_removes_both_orphaned_actions_and_observations() -> None:
    """Test that both orphaned actions and observations are removed."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")  # Orphaned
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_3")  # Orphaned

    current_view = [action1, action2, obs1, obs2]
    all_events = [action1, action2, obs1, obs2]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert action2.id in to_remove
    assert obs2.id in to_remove
    assert action1.id not in to_remove
    assert obs1.id not in to_remove


def test_enforce_user_reject_observation_counts_as_match() -> None:
    """Test that UserRejectObservation matches with ActionEvent."""
    action = create_action_event("response_1", "call_1")
    reject = create_user_reject_observation("call_1")

    current_view = [action, reject]
    all_events = [action, reject]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Both should be kept
    assert len(to_remove) == 0


def test_enforce_agent_error_event_counts_as_match() -> None:
    """Test that AgentErrorEvent matches with ActionEvent."""
    action = create_action_event("response_1", "call_1")
    error = create_agent_error_event("call_1")

    current_view = [action, error]
    all_events = [action, error]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Both should be kept
    assert len(to_remove) == 0


def test_enforce_multiple_observation_types() -> None:
    """Test with mix of ObservationEvent, UserRejectObservation, and AgentErrorEvent."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    obs1 = create_observation_event("call_1")
    reject2 = create_user_reject_observation("call_2")
    error3 = create_agent_error_event("call_3")

    current_view = [action1, action2, action3, obs1, reject2, error3]
    all_events = [action1, action2, action3, obs1, reject2, error3]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    # All matched, nothing to remove
    assert len(to_remove) == 0


def test_enforce_empty_view() -> None:
    """Test enforce with empty view."""
    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce([], [])

    assert len(to_remove) == 0


def test_enforce_only_messages() -> None:
    """Test that messages are not affected by tool call matching."""
    msg1 = message_event("Message 1")
    msg2 = message_event("Message 2")

    current_view = [msg1, msg2]
    all_events = [msg1, msg2]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_mixed_with_messages() -> None:
    """Test that messages are preserved while orphaned events are removed."""
    msg1 = message_event("Start")
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")  # Orphaned
    obs1 = create_observation_event("call_1")
    msg2 = message_event("End")

    current_view = [msg1, action1, action2, obs1, msg2]
    all_events = [msg1, action1, action2, obs1, msg2]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert action2.id in to_remove
    assert msg1.id not in to_remove
    assert msg2.id not in to_remove


def test_enforce_cascading_removal() -> None:
    """Test that removing actions can cascade to their observations and vice versa.

    Note: This property doesn't do cascading - each element is independently checked.
    Cascading would require multiple passes or composition with other properties.
    """
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")

    # View has action1 and obs2, but missing their pairs
    current_view = [action1, obs2]
    all_events = [action1, action2, obs1, obs2]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Both should be removed as orphans
    assert action1.id in to_remove  # No obs1 in view
    assert obs2.id in to_remove  # No action2 in view


def test_enforce_same_tool_call_id_different_events() -> None:
    """Test that matching works even with same tool_call_id on different events."""
    action = create_action_event("response_1", "call_1")
    obs = create_observation_event("call_1")

    current_view = [action, obs]
    all_events = [action, obs]

    prop = ToolCallMatchingProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


# ============================================================================
# Tests for manipulation_indices() method
# ============================================================================


def test_manipulation_indices_all_valid() -> None:
    """Test that all indices are valid for tool call matching property.

    Unlike batch atomicity and tool loop atomicity, this property doesn't
    restrict manipulation indices. It validates through filtering instead.
    """
    action = create_action_event("response_1", "call_1")
    obs = create_observation_event("call_1")

    events = [action, obs]

    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices(events, events)

    # All indices should be valid
    assert indices == {0, 1, 2}


def test_manipulation_indices_empty_events() -> None:
    """Test with empty event list."""
    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices([], [])

    assert indices == {0}


def test_manipulation_indices_complex_scenario() -> None:
    """Test that all indices are valid regardless of event complexity."""
    msg1 = message_event("Start")

    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")

    msg2 = message_event("End")

    events = [msg1, action1, action2, obs1, obs2, msg2]

    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices(events, events)

    # All indices are valid
    assert indices == {0, 1, 2, 3, 4, 5, 6}


def test_manipulation_indices_orphaned_events() -> None:
    """Test that orphaned events don't affect manipulation indices."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")  # Orphaned
    obs1 = create_observation_event("call_1")

    events = [action1, action2, obs1]

    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices(events, events)

    # All indices are still valid
    assert indices == {0, 1, 2, 3}


def test_manipulation_indices_only_messages() -> None:
    """Test with only message events."""
    msg1 = message_event("Message 1")
    msg2 = message_event("Message 2")
    msg3 = message_event("Message 3")

    events = [msg1, msg2, msg3]

    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices(events, events)

    assert indices == {0, 1, 2, 3}


def test_manipulation_indices_with_different_observation_types() -> None:
    """Test that different observation types don't affect indices."""
    action1 = create_action_event("response_1", "call_1")
    action2 = create_action_event("response_1", "call_2")
    action3 = create_action_event("response_1", "call_3")

    obs = create_observation_event("call_1")
    reject = create_user_reject_observation("call_2")
    error = create_agent_error_event("call_3")

    events = [action1, action2, action3, obs, reject, error]

    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices(events, events)

    # All indices valid
    assert indices == {0, 1, 2, 3, 4, 5, 6}


def test_manipulation_indices_single_event() -> None:
    """Test with a single event."""
    action = create_action_event("response_1", "call_1")

    prop = ToolCallMatchingProperty()
    indices = prop.manipulation_indices([action], [action])

    assert indices == {0, 1}
