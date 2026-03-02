"""Tests for ToolResultUniquenessProperty.

This module tests that duplicate tool results for the same tool_call_id
are properly deduplicated, preferring ObservationEvent over AgentErrorEvent.
"""

from unittest.mock import create_autospec

from openhands.sdk.context.view.properties.tool_result_uniqueness import (
    ToolResultUniquenessProperty,
)
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)
from tests.sdk.context.view.properties.conftest import message_event


class TestToolResultUniquenessPropertyEnforcement:
    """Tests for the enforce method of ToolResultUniquenessProperty."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolResultUniquenessProperty()

    def test_empty_list(self) -> None:
        """Test enforce with empty event list."""
        result = self.property.enforce([], [])
        assert result == set()

    def test_no_duplicates(self) -> None:
        """Test enforce when there are no duplicate tool_call_ids."""
        obs1 = create_autospec(ObservationEvent, instance=True)
        obs1.tool_call_id = "call_1"
        obs1.id = "obs_1"

        obs2 = create_autospec(ObservationEvent, instance=True)
        obs2.tool_call_id = "call_2"
        obs2.id = "obs_2"

        events: list[LLMConvertibleEvent] = [
            message_event("Start"),
            obs1,
            obs2,
            message_event("End"),
        ]

        result = self.property.enforce(events, events)
        assert result == set()

    def test_duplicate_observation_events(self) -> None:
        """Test that duplicate ObservationEvents keep the later one."""
        obs1 = create_autospec(ObservationEvent, instance=True)
        obs1.tool_call_id = "call_1"
        obs1.id = "obs_1"

        obs2 = create_autospec(ObservationEvent, instance=True)
        obs2.tool_call_id = "call_1"  # Same tool_call_id!
        obs2.id = "obs_2"

        events: list[LLMConvertibleEvent] = [obs1, obs2]

        result = self.property.enforce(events, events)
        # obs1 should be removed, obs2 (later) should be kept
        assert result == {"obs_1"}

    def test_observation_event_preferred_over_agent_error(self) -> None:
        """Test that ObservationEvent is preferred over AgentErrorEvent."""
        # This is the main bug scenario: AgentErrorEvent created on restart,
        # then ObservationEvent arrives later with actual result
        agent_error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart occurred while tool was running",
        )

        obs = create_autospec(ObservationEvent, instance=True)
        obs.tool_call_id = "call_1"  # Same tool_call_id!
        obs.id = "obs_1"

        events: list[LLMConvertibleEvent] = [agent_error, obs]

        result = self.property.enforce(events, events)
        # AgentErrorEvent should be removed, ObservationEvent kept
        assert result == {agent_error.id}

    def test_agent_error_before_observation_event(self) -> None:
        """Test AgentErrorEvent followed by ObservationEvent (restart scenario)."""
        # Simulates: restart creates AgentErrorEvent, then actual result arrives
        action = create_autospec(ActionEvent, instance=True)
        action.tool_call_id = "call_1"
        action.id = "action_1"
        action.llm_response_id = "response_1"

        agent_error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="A restart occurred while this tool was in progress.",
        )

        obs = create_autospec(ObservationEvent, instance=True)
        obs.tool_call_id = "call_1"
        obs.id = "obs_1"

        events: list[LLMConvertibleEvent] = [
            message_event("User message"),
            action,
            agent_error,
            obs,  # Actual result arrives later
        ]

        result = self.property.enforce(events, events)
        # AgentErrorEvent should be removed since we have actual ObservationEvent
        assert result == {agent_error.id}

    def test_multiple_agent_errors_keep_last(self) -> None:
        """Test that when only AgentErrorEvents exist, the last one is kept."""
        error1 = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="First error",
        )

        error2 = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Second error",
        )

        events: list[LLMConvertibleEvent] = [error1, error2]

        result = self.property.enforce(events, events)
        # First error should be removed, second (later) should be kept
        assert result == {error1.id}

    def test_user_reject_observation_handling(self) -> None:
        """Test that UserRejectObservation is handled correctly."""
        reject = UserRejectObservation(
            tool_name="terminal",
            tool_call_id="call_1",
            action_id="action_1",
            rejection_reason="User rejected",
        )

        obs = create_autospec(ObservationEvent, instance=True)
        obs.tool_call_id = "call_1"
        obs.id = "obs_1"

        events: list[LLMConvertibleEvent] = [reject, obs]

        result = self.property.enforce(events, events)
        # ObservationEvent is preferred over UserRejectObservation
        assert result == {reject.id}

    def test_mixed_scenario_multiple_tool_calls(self) -> None:
        """Test with multiple tool calls, some with duplicates."""
        # Tool call 1: has duplicate (error + observation)
        error1 = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart error",
        )
        obs1 = create_autospec(ObservationEvent, instance=True)
        obs1.tool_call_id = "call_1"
        obs1.id = "obs_1"

        # Tool call 2: single observation (no duplicate)
        obs2 = create_autospec(ObservationEvent, instance=True)
        obs2.tool_call_id = "call_2"
        obs2.id = "obs_2"

        # Tool call 3: single error (no duplicate)
        error3 = AgentErrorEvent(
            tool_name="file_editor",
            tool_call_id="call_3",
            error="Tool not found",
        )

        events: list[LLMConvertibleEvent] = [
            message_event("Start"),
            error1,
            obs1,
            obs2,
            error3,
        ]

        result = self.property.enforce(events, events)
        # Only error1 should be removed (duplicate with obs1)
        assert result == {error1.id}


class TestToolResultUniquenessPropertyManipulationIndices:
    """Tests for the manipulation_indices method."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolResultUniquenessProperty()

    def test_complete_indices_returned(self) -> None:
        """Test that manipulation indices are complete (no restrictions)."""
        obs = create_autospec(ObservationEvent, instance=True)
        obs.tool_call_id = "call_1"
        obs.id = "obs_1"

        events: list[LLMConvertibleEvent] = [
            message_event("Start"),
            obs,
            message_event("End"),
        ]

        result = self.property.manipulation_indices(events)
        # Should have indices 0, 1, 2, 3 (all positions)
        assert 0 in result
        assert 1 in result
        assert 2 in result
        assert 3 in result
