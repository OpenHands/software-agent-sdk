"""Tests for ToolLoopAtomicityProperty.

This module tests that the ToolLoopAtomicityProperty correctly ensures tool loops
(sequences of action/observation pairs) form atomic units.
"""

from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.tool_loop_atomicity import (
    ToolLoopAtomicityProperty,
)
from openhands.sdk.event import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    ObservationEvent,
)
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
    event_id: str,
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
        id=event_id,
        thought=[TextContent(text="Test thought")],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
        source="agent",
    )


def create_observation_event(
    event_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    content: str = "Success",
) -> ObservationEvent:
    """Helper to create an ObservationEvent."""
    observation = MCPToolObservation.from_text(
        text=content,
        tool_name=tool_name,
    )
    return ObservationEvent(
        id=event_id,
        observation=observation,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id="action_event_id",
        source="environment",
    )


def create_message_event(event_id: str, content: str) -> LLMConvertibleEvent:
    """Helper to create a non-tool-loop event (MessageEvent)."""
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


class TestToolLoopAtomicityPropertyBase:
    """Base class for ToolLoopAtomicityProperty test suites."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolLoopAtomicityProperty()


class TestToolLoopAtomicityPropertyEnforcement(TestToolLoopAtomicityPropertyBase):
    """Tests for ToolLoopAtomicityProperty enforcement."""

    def test_partial_tool_loop_forgotten(self) -> None:
        """Test that if one event in a tool loop is forgotten, all events in that loop
        are forgotten.

        This simulates the scenario where condensation forgets some but not all
        events from a tool loop. The tool loop atomicity logic should ensure that all
        events in the loop are removed.
        """
        # Create a tool loop: action -> observation -> action -> observation
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Current view has action_1, observation_1 forgotten but action_2, obs_2 kept
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # action_2 and obs_2 should be forgotten due to tool loop atomicity
        assert "action_2" in events_to_remove
        assert "obs_2" in events_to_remove

    def test_complete_tool_loop_forgotten(self) -> None:
        """Test that when all events in a tool loop are forgotten, they're removed."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
        ]

        # Current view has no events (all forgotten)
        current_view_events: list[LLMConvertibleEvent] = []

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing more to remove since the tool loop is already gone
        assert len(events_to_remove) == 0

    def test_no_forgetting_preserves_tool_loop(self) -> None:
        """Test that when no events in a tool loop are forgotten, all are preserved."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Current view has all events
        current_view_events: list[LLMConvertibleEvent] = list(all_events)

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing should be removed
        assert len(events_to_remove) == 0

    def test_tool_loop_between_non_tool_loop_events(self) -> None:
        """Test that tool loops are bounded by non-tool-loop events."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_message_event("msg_1", "User message"),
            # Tool loop starts
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
            # Tool loop ends
            create_message_event("msg_2", "Another user message"),
        ]

        # Current view: first action forgotten but rest kept
        current_view_events: list[LLMConvertibleEvent] = [
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
            create_message_event("msg_2", "Another user message"),
        ]

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # All remaining tool loop events should be removed
        assert "obs_1" in events_to_remove
        assert "action_2" in events_to_remove
        assert "obs_2" in events_to_remove
        # Message should be preserved
        assert "msg_2" not in events_to_remove

    def test_first_event_of_tool_loop_forgotten(self) -> None:
        """Test that forgetting first event causes entire tool loop to be forgotten."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Current view has action_1 forgotten
        current_view_events: list[LLMConvertibleEvent] = [
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # All tool loop events should be forgotten
        assert "obs_1" in events_to_remove
        assert "action_2" in events_to_remove
        assert "obs_2" in events_to_remove

    def test_middle_event_of_tool_loop_forgotten(self) -> None:
        """Test that forgetting middle event causes entire tool loop to be forgotten."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Current view has observation_1 forgotten
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # All tool loop events in the view should be forgotten
        # Note: action_1 is not in current_view_events, so it can't be removed
        assert "action_2" in events_to_remove
        assert "obs_2" in events_to_remove

    def test_multiple_separate_tool_loops(self) -> None:
        """Test that multiple separate tool loops are handled independently."""
        all_events: Sequence[LLMConvertibleEvent] = [
            # First tool loop
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            # Gap (non-tool-loop event)
            create_message_event("msg_1", "User message"),
            # Second tool loop
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Current view: first tool loop complete, second partial (only obs, no action)
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_message_event("msg_1", "User message"),
            create_observation_event("obs_2", "call_2"),
        ]

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Second tool loop's observation should be removed
        # (the action isn't even in the view)
        assert "obs_2" in events_to_remove
        # First tool loop should be preserved
        assert "action_1" not in events_to_remove
        assert "obs_1" not in events_to_remove
        # Message should be preserved
        assert "msg_1" not in events_to_remove

    def test_single_action_observation_pair(self) -> None:
        """Test that a single action/observation pair works correctly."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
        ]

        # Current view has both events
        current_view_events: list[LLMConvertibleEvent] = list(all_events)

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing should be removed
        assert len(events_to_remove) == 0

    def test_single_action_forgotten(self) -> None:
        """Test that a forgotten single-pair tool loop is handled correctly."""
        all_events: Sequence[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
        ]

        # Current view has no events (forgotten)
        current_view_events: list[LLMConvertibleEvent] = []

        # Enforce tool loop atomicity
        events_to_remove = self.property.enforce(current_view_events, all_events)

        # Nothing more to remove
        assert len(events_to_remove) == 0


class TestToolLoopAtomicityPropertyManipulationIndices(
    TestToolLoopAtomicityPropertyBase
):
    """Tests for ToolLoopAtomicityProperty manipulation indices."""

    def test_no_manipulation_within_tool_loop(self) -> None:
        """Test that events in a tool loop cannot be split by manipulation."""
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        indices = self.property.manipulation_indices(current_view_events)

        # Index 1 (between action_1 and obs_1) should not be manipulatable
        assert 1 not in indices
        # Index 2 (between obs_1 and action_2) should not be manipulatable
        assert 2 not in indices
        # Index 3 (between action_2 and obs_2) should not be manipulatable
        assert 3 not in indices

    def test_manipulation_allowed_between_tool_loops(self) -> None:
        """Test that manipulation is allowed between separate tool loops."""
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_message_event("msg_1", "User message"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        indices = self.property.manipulation_indices(current_view_events)

        # Index 2 (between first tool loop and message) should be manipulatable
        assert 2 in indices
        # Index 5 (between second tool loop and end) should be manipulatable
        assert 5 in indices

    def test_manipulation_allowed_before_first_tool_loop(self) -> None:
        """Test that manipulation is allowed before the first tool loop."""
        current_view_events: list[LLMConvertibleEvent] = [
            create_message_event("msg_1", "User message"),
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
        ]

        indices = self.property.manipulation_indices(current_view_events)

        # Index 0 (before message) should be manipulatable
        assert 0 in indices
        # Note: Index 1 is removed because entering a tool loop -
        # cannot manipulate at the start

    def test_single_event_complete_indices(self) -> None:
        """Test that a single event has complete manipulation indices."""
        current_view_events: list[LLMConvertibleEvent] = [
            create_message_event("msg_1", "User message"),
        ]

        indices = self.property.manipulation_indices(current_view_events)
        assert indices == ManipulationIndices.complete(current_view_events)

    def test_empty_events_complete_indices(self) -> None:
        """Test that an empty event list has complete manipulation indices."""
        current_view_events: list[LLMConvertibleEvent] = []

        indices = self.property.manipulation_indices(current_view_events)
        assert indices == ManipulationIndices.complete(current_view_events)

    def test_tool_loop_with_message_breaks_at_boundary(self) -> None:
        """Test that a message event breaks the tool loop."""
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1"),
            create_observation_event("obs_1", "call_1"),
            create_message_event("msg_1", "User message"),
            create_action_event("action_2", "resp_2", "call_2"),
            create_observation_event("obs_2", "call_2"),
        ]

        indices = self.property.manipulation_indices(current_view_events)

        # Indices within first tool loop should not be manipulatable
        assert 1 not in indices
        # Index at the boundary (between obs_1 and msg_1) should be manipulatable
        assert 2 in indices
        # Indices within second tool loop should not be manipulatable
        assert 3 in indices

    def test_parallel_actions_in_tool_loop(self) -> None:
        """Test that parallel actions (same response) are in the same tool loop."""
        # Two actions from same response (parallel) followed by observations
        current_view_events: list[LLMConvertibleEvent] = [
            create_action_event("action_1", "resp_1", "call_1a"),
            create_action_event("action_1b", "resp_1", "call_1b"),
            create_observation_event("obs_1a", "call_1a"),
            create_observation_event("obs_1b", "call_1b"),
        ]

        indices = self.property.manipulation_indices(current_view_events)

        # No indices within the tool loop should be manipulatable
        assert 1 not in indices
        assert 2 not in indices
        assert 3 not in indices
