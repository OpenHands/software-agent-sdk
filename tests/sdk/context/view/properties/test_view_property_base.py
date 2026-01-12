"""Tests for ViewPropertyBase utility functions."""

from openhands.sdk.context.view.properties.base import ViewPropertyBase
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


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


# ============================================================================
# Tests for _build_batches() utility function
# ============================================================================


def test_build_batches_empty_list() -> None:
    """Test _build_batches with empty event list."""
    result = ViewPropertyBase._build_batches([])
    assert result == {}


def test_build_batches_no_action_events() -> None:
    """Test _build_batches with no ActionEvents."""
    events = [
        message_event("Hello"),
        create_observation_event("call_1"),
    ]
    result = ViewPropertyBase._build_batches(events)
    assert result == {}


def test_build_batches_single_action() -> None:
    """Test _build_batches with single ActionEvent."""
    action = create_action_event("resp_1", "call_1")
    events = [action]

    result = ViewPropertyBase._build_batches(events)

    assert len(result) == 1
    assert "resp_1" in result
    assert result["resp_1"] == [action.id]


def test_build_batches_multiple_actions_same_response() -> None:
    """Test _build_batches with multiple actions from same LLM response."""
    action1 = create_action_event("resp_1", "call_1")
    action2 = create_action_event("resp_1", "call_2")
    action3 = create_action_event("resp_1", "call_3")
    events = [action1, action2, action3]

    result = ViewPropertyBase._build_batches(events)

    assert len(result) == 1
    assert "resp_1" in result
    assert result["resp_1"] == [action1.id, action2.id, action3.id]


def test_build_batches_multiple_actions_different_responses() -> None:
    """Test _build_batches with actions from different LLM responses."""
    action1 = create_action_event("resp_1", "call_1")
    action2 = create_action_event("resp_2", "call_2")
    action3 = create_action_event("resp_3", "call_3")
    events = [action1, action2, action3]

    result = ViewPropertyBase._build_batches(events)

    assert len(result) == 3
    assert result["resp_1"] == [action1.id]
    assert result["resp_2"] == [action2.id]
    assert result["resp_3"] == [action3.id]


def test_build_batches_mixed_event_types() -> None:
    """Test _build_batches with mixed event types."""
    msg = message_event("User message")
    action1 = create_action_event("resp_1", "call_1")
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("resp_2", "call_2")
    obs2 = create_observation_event("call_2")

    events = [msg, action1, obs1, action2, obs2]

    result = ViewPropertyBase._build_batches(events)

    assert len(result) == 2
    assert result["resp_1"] == [action1.id]
    assert result["resp_2"] == [action2.id]


def test_build_batches_parallel_calls() -> None:
    """Test _build_batches with parallel tool calls (same llm_response_id)."""
    action1 = create_action_event("resp_1", "call_1a")
    action2 = create_action_event("resp_1", "call_1b")
    action3 = create_action_event("resp_1", "call_1c")
    obs1 = create_observation_event("call_1a")
    obs2 = create_observation_event("call_1b")
    obs3 = create_observation_event("call_1c")

    events = [action1, action2, action3, obs1, obs2, obs3]

    result = ViewPropertyBase._build_batches(events)

    assert len(result) == 1
    assert result["resp_1"] == [action1.id, action2.id, action3.id]


def test_build_batches_interleaved_batches() -> None:
    """Test _build_batches with interleaved batches."""
    action1a = create_action_event("resp_1", "call_1a")
    action2a = create_action_event("resp_2", "call_2a")
    action1b = create_action_event("resp_1", "call_1b")
    action2b = create_action_event("resp_2", "call_2b")

    events = [action1a, action2a, action1b, action2b]

    result = ViewPropertyBase._build_batches(events)

    assert len(result) == 2
    assert result["resp_1"] == [action1a.id, action1b.id]
    assert result["resp_2"] == [action2a.id, action2b.id]


# ============================================================================
# Tests for _build_event_id_to_index() utility function
# ============================================================================


def test_build_event_id_to_index_empty_list() -> None:
    """Test _build_event_id_to_index with empty event list."""
    result = ViewPropertyBase._build_event_id_to_index([])
    assert result == {}


def test_build_event_id_to_index_single_event() -> None:
    """Test _build_event_id_to_index with single event."""
    event = message_event("Hello")
    events = [event]

    result = ViewPropertyBase._build_event_id_to_index(events)

    assert len(result) == 1
    assert result[event.id] == 0


def test_build_event_id_to_index_multiple_events() -> None:
    """Test _build_event_id_to_index with multiple events."""
    event1 = message_event("Hello")
    event2 = create_action_event("resp_1", "call_1")
    event3 = create_observation_event("call_1")
    event4 = message_event("Goodbye")

    events = [event1, event2, event3, event4]

    result = ViewPropertyBase._build_event_id_to_index(events)

    assert len(result) == 4
    assert result[event1.id] == 0
    assert result[event2.id] == 1
    assert result[event3.id] == 2
    assert result[event4.id] == 3


def test_build_event_id_to_index_preserves_order() -> None:
    """Test that _build_event_id_to_index preserves event order."""
    events = [create_action_event(f"resp_{i}", f"call_{i}") for i in range(10)]

    result = ViewPropertyBase._build_event_id_to_index(events)

    assert len(result) == 10
    for idx, event in enumerate(events):
        assert result[event.id] == idx


def test_build_event_id_to_index_different_event_types() -> None:
    """Test _build_event_id_to_index with different event types."""
    msg1 = message_event("User 1")
    action1 = create_action_event("resp_1", "call_1")
    action2 = create_action_event("resp_1", "call_2")  # Parallel call
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")
    msg2 = message_event("User 2")

    events = [msg1, action1, action2, obs1, obs2, msg2]

    result = ViewPropertyBase._build_event_id_to_index(events)

    assert len(result) == 6
    assert result[msg1.id] == 0
    assert result[action1.id] == 1
    assert result[action2.id] == 2
    assert result[obs1.id] == 3
    assert result[obs2.id] == 4
    assert result[msg2.id] == 5


def test_build_event_id_to_index_unique_ids() -> None:
    """Test that each event has a unique ID and index."""
    events = [
        message_event("Message 1"),
        create_action_event("resp_1", "call_1"),
        create_observation_event("call_1"),
        message_event("Message 2"),
    ]

    result = ViewPropertyBase._build_event_id_to_index(events)

    # All event IDs should be unique
    assert len(result) == len(events)

    # All indices should be unique and in range [0, len(events))
    indices = list(result.values())
    assert len(set(indices)) == len(indices)
    assert min(indices) == 0
    assert max(indices) == len(events) - 1
