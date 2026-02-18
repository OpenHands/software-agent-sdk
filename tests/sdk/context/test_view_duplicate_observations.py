"""Test for duplicate tool_call_id observations causing API errors.

This test reproduces the issue where multiple ObservationBaseEvent types
(e.g., AgentErrorEvent and ObservationEvent) share the same tool_call_id,
resulting in duplicate tool_result blocks being sent to the LLM API.

The error manifests as:
    "each tool_use must have a single result. Found multiple `tool_result`
    blocks with id: <tool_call_id>"

Root cause:
When a server restart occurs while a tool is in progress, the EventService
creates an AgentErrorEvent to notify the agent. However, if the tool
eventually completes (or was already completing), an ObservationEvent is
also recorded. Both events share the same tool_call_id, causing duplicate
tool_result blocks when converted to LLM messages.
"""

from unittest.mock import MagicMock, create_autospec

from openhands.sdk.context.view import View
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationBaseEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.tool.schema import Observation


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def test_duplicate_observations_agent_error_then_observation() -> None:
    """Test that duplicate observations with same tool_call_id are deduplicated.

    Reproduces the restart scenario where:
    1. ActionEvent starts a tool
    2. Server restart creates AgentErrorEvent for the unmatched action
    3. Tool completes, creating ObservationEvent

    Both AgentErrorEvent and ObservationEvent have the same tool_call_id,
    which previously caused duplicate tool_result blocks.
    """
    tool_call_id = "toolu_01EvREYhc5WD2xswPAvEc8ir"

    # Create an ActionEvent
    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = tool_call_id
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"
    action_event.thinking_blocks = []

    # Create AgentErrorEvent (from restart)
    agent_error = AgentErrorEvent(
        error=(
            "A restart occurred while this tool was in progress. "
            "This may indicate a fatal memory error or system crash. "
            "The tool execution was interrupted and did not complete."
        ),
        tool_name="terminal",
        tool_call_id=tool_call_id,
    )

    # Create ObservationEvent (tool eventually completed)
    observation_event = create_autospec(ObservationEvent, instance=True)
    observation_event.tool_call_id = tool_call_id
    observation_event.id = "obs_1"

    events = [
        message_event("Start"),
        action_event,
        agent_error,  # Error first
        observation_event,  # Then actual result
        message_event("End"),
    ]

    # Filter should keep only one observation per tool_call_id
    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # Count how many observations with this tool_call_id are in the result
    # Use isinstance to properly check for tool_call_id attribute
    observations_with_tool_call_id = [
        e
        for e in result
        if (
            isinstance(e, (ActionEvent, ObservationBaseEvent))
            and e.tool_call_id == tool_call_id
        )
    ]
    action_events = [e for e in observations_with_tool_call_id if e is action_event]
    non_action_events = [
        e for e in observations_with_tool_call_id if e is not action_event
    ]

    # Should have exactly 1 ActionEvent and 1 observation (not 2)
    assert len(action_events) == 1, "Should have exactly 1 ActionEvent"
    assert len(non_action_events) == 1, (
        f"Should have exactly 1 observation, got {len(non_action_events)}"
    )

    # Verify other events are preserved
    message_events = [e for e in result if isinstance(e, MessageEvent)]
    assert len(message_events) == 2


def test_duplicate_observations_observation_then_agent_error() -> None:
    """Test deduplication when ObservationEvent comes before AgentErrorEvent.

    This tests the reverse order, which could happen in race conditions.
    """
    tool_call_id = "call_duplicate"

    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = tool_call_id
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"
    action_event.thinking_blocks = []

    observation_event = create_autospec(ObservationEvent, instance=True)
    observation_event.tool_call_id = tool_call_id
    observation_event.id = "obs_1"

    agent_error = AgentErrorEvent(
        error="Tool execution failed due to restart",
        tool_name="terminal",
        tool_call_id=tool_call_id,
    )

    events = [
        action_event,
        observation_event,  # Observation first
        agent_error,  # Then error
    ]

    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # Count observations
    observations = [
        e
        for e in result
        if isinstance(e, ObservationBaseEvent) and e.tool_call_id == tool_call_id
    ]

    assert len(observations) == 1, (
        f"Should have exactly 1 observation, got {len(observations)}"
    )


def test_multiple_tool_calls_with_one_duplicate() -> None:
    """Test that deduplication only affects duplicate tool_call_ids.

    When there are multiple tool calls, only the one with duplicate
    observations should be affected.
    """
    # First tool call - normal (no duplicates)
    action_1 = create_autospec(ActionEvent, instance=True)
    action_1.tool_call_id = "call_1"
    action_1.id = "action_1"
    action_1.llm_response_id = "response_1"
    action_1.thinking_blocks = []

    obs_1 = create_autospec(ObservationEvent, instance=True)
    obs_1.tool_call_id = "call_1"
    obs_1.id = "obs_1"

    # Second tool call - duplicate observations
    action_2 = create_autospec(ActionEvent, instance=True)
    action_2.tool_call_id = "call_2"
    action_2.id = "action_2"
    action_2.llm_response_id = "response_2"
    action_2.thinking_blocks = []

    error_2 = AgentErrorEvent(
        error="Restart error",
        tool_name="terminal",
        tool_call_id="call_2",
    )

    obs_2 = create_autospec(ObservationEvent, instance=True)
    obs_2.tool_call_id = "call_2"
    obs_2.id = "obs_2"

    # Third tool call - normal (no duplicates)
    action_3 = create_autospec(ActionEvent, instance=True)
    action_3.tool_call_id = "call_3"
    action_3.id = "action_3"
    action_3.llm_response_id = "response_3"
    action_3.thinking_blocks = []

    obs_3 = create_autospec(ObservationEvent, instance=True)
    obs_3.tool_call_id = "call_3"
    obs_3.id = "obs_3"

    events = [
        action_1,
        obs_1,
        action_2,
        error_2,
        obs_2,
        action_3,
        obs_3,
    ]

    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # call_1 should have 1 action + 1 observation
    call_1_events = [
        e
        for e in result
        if isinstance(e, (ActionEvent, ObservationBaseEvent))
        and e.tool_call_id == "call_1"
    ]
    assert len(call_1_events) == 2

    # call_2 should have 1 action + 1 observation (deduplicated from 2)
    call_2_events = [
        e
        for e in result
        if isinstance(e, (ActionEvent, ObservationBaseEvent))
        and e.tool_call_id == "call_2"
    ]
    assert len(call_2_events) == 2, (
        f"call_2 should have 2 events, got {len(call_2_events)}"
    )

    # call_3 should have 1 action + 1 observation
    call_3_events = [
        e
        for e in result
        if isinstance(e, (ActionEvent, ObservationBaseEvent))
        and e.tool_call_id == "call_3"
    ]
    assert len(call_3_events) == 2


def test_view_from_events_deduplicates_observations() -> None:
    """Test that View.from_events properly deduplicates observations.

    This tests the full View creation flow to ensure deduplication
    is applied correctly.
    """
    tool_call_id = "call_dup"

    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = tool_call_id
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"
    action_event.thinking_blocks = []

    agent_error = AgentErrorEvent(
        error="Restart error",
        tool_name="terminal",
        tool_call_id=tool_call_id,
    )

    # Create a proper ObservationEvent with the observation field
    mock_observation = MagicMock(spec=Observation)
    mock_observation.to_llm_content = [TextContent(text="Tool output")]

    observation_event = ObservationEvent(
        source="environment",
        tool_name="terminal",
        tool_call_id=tool_call_id,
        observation=mock_observation,
        action_id="action_1",
    )

    events = [
        message_event("Start"),
        action_event,
        agent_error,
        observation_event,
        message_event("End"),
    ]

    view = View.from_events(events)

    # Count observations with this tool_call_id
    observations = [
        e
        for e in view.events
        if isinstance(e, ObservationBaseEvent) and e.tool_call_id == tool_call_id
    ]

    assert len(observations) == 1, (
        f"View should contain exactly 1 observation for tool_call_id, "
        f"got {len(observations)}"
    )


def test_prefer_observation_event_over_error() -> None:
    """Test that ObservationEvent is preferred over AgentErrorEvent.

    When deduplicating, we should keep the ObservationEvent (actual result)
    rather than the AgentErrorEvent (error notification).
    """
    tool_call_id = "call_prefer"

    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = tool_call_id
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"
    action_event.thinking_blocks = []

    agent_error = AgentErrorEvent(
        error="Restart error - tool was interrupted",
        tool_name="terminal",
        tool_call_id=tool_call_id,
    )

    mock_observation = MagicMock(spec=Observation)
    mock_observation.to_llm_content = [TextContent(text="Tool output")]

    observation_event = ObservationEvent(
        source="environment",
        tool_name="terminal",
        tool_call_id=tool_call_id,
        observation=mock_observation,
        action_id="action_1",
    )

    # Error comes before observation
    events = [
        action_event,
        agent_error,
        observation_event,
    ]

    result = View._filter_unmatched_tool_calls(events, events)  # type: ignore

    # Find the kept observation
    kept_observation = [
        e
        for e in result
        if isinstance(e, ObservationBaseEvent) and e.tool_call_id == tool_call_id
    ]

    assert len(kept_observation) == 1
    # Should prefer ObservationEvent over AgentErrorEvent
    assert isinstance(kept_observation[0], ObservationEvent), (
        f"Should prefer ObservationEvent, got {type(kept_observation[0])}"
    )


def test_events_to_messages_with_deduplicated_view() -> None:
    """Test that events_to_messages works correctly after deduplication.

    This ensures that after View.from_events deduplicates observations,
    the resulting events can be converted to LLM messages without
    duplicate tool_result blocks.
    """
    from openhands.sdk.llm import MessageToolCall

    tool_call_id = "call_messages"

    action_event = create_autospec(ActionEvent, instance=True)
    action_event.tool_call_id = tool_call_id
    action_event.id = "action_1"
    action_event.llm_response_id = "response_1"
    action_event.thinking_blocks = []
    action_event.to_llm_message.return_value = Message(
        role="assistant",
        content=[TextContent(text="Running command")],
        tool_calls=[
            MessageToolCall(
                id=tool_call_id, name="terminal", arguments="{}", origin="completion"
            )
        ],
    )

    agent_error = AgentErrorEvent(
        error="Restart error",
        tool_name="terminal",
        tool_call_id=tool_call_id,
    )

    mock_observation = MagicMock(spec=Observation)
    mock_observation.to_llm_content = [TextContent(text="Tool output")]

    observation_event = ObservationEvent(
        source="environment",
        tool_name="terminal",
        tool_call_id=tool_call_id,
        observation=mock_observation,
        action_id="action_1",
    )

    events = [
        action_event,
        agent_error,
        observation_event,
    ]

    view = View.from_events(events)

    # Convert to messages
    messages = LLMConvertibleEvent.events_to_messages(view.events)

    # Count tool messages with this tool_call_id
    tool_messages = [
        m for m in messages if m.role == "tool" and m.tool_call_id == tool_call_id
    ]

    assert len(tool_messages) == 1, (
        f"Should have exactly 1 tool message, got {len(tool_messages)}"
    )
