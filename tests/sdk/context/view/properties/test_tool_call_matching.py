"""Tests for ToolCallMatchingProperty.

This module tests that actions and observations are properly paired by tool_call_id.
The property ensures unmatched actions and observations are filtered out.
"""

from unittest.mock import create_autospec

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.tool_call_matching import (
    ToolCallMatchingProperty,
)
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)
from tests.sdk.context.view.properties.conftest import message_event


class TestToolCallMatchingBase:
    """Base class for ToolCallMatchingProperty test suites."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolCallMatchingProperty()


class TestToolCallMatchingPropertyEnforcement(TestToolCallMatchingBase):
    """Tests for the enforce method of ToolCallMatchingProperty."""

    def test_empty_list(self) -> None:
        """Test enforce with empty event list."""
        result = self.property.enforce([], [])
        assert result == set()

    def test_no_tool_events(self) -> None:
        """Test enforce with no tool events."""
        message1 = message_event("First message")
        message2 = message_event("Second message")

        events: list[LLMConvertibleEvent] = [message1, message2]
        result = self.property.enforce(events, events)

        # No tool events, nothing to remove
        assert result == set()

    def test_matched_pairs(self) -> None:
        """Test enforce with matched tool call pairs."""
        message = message_event("Test message")

        # Matched pair 1
        action_event_1 = create_autospec(ActionEvent, instance=True)
        action_event_1.tool_call_id = "call_1"
        action_event_1.id = "action_1"
        action_event_1.llm_response_id = "response_1"

        observation_event_1 = create_autospec(ObservationEvent, instance=True)
        observation_event_1.tool_call_id = "call_1"
        observation_event_1.id = "obs_1"

        # Matched pair 2
        action_event_2 = create_autospec(ActionEvent, instance=True)
        action_event_2.tool_call_id = "call_2"
        action_event_2.id = "action_2"
        action_event_2.llm_response_id = "response_2"

        observation_event_2 = create_autospec(ObservationEvent, instance=True)
        observation_event_2.tool_call_id = "call_2"
        observation_event_2.id = "obs_2"

        events: list[LLMConvertibleEvent] = [
            message,
            action_event_1,
            observation_event_1,
            action_event_2,
            observation_event_2,
        ]

        result = self.property.enforce(events, events)

        # All events should be kept (all tool calls are matched)
        assert result == set()
        assert action_event_1.id not in result
        assert observation_event_1.id not in result

    def test_unmatched_action(self) -> None:
        """Test enforce with unmatched ActionEvent."""
        message = message_event("Test message")

        # Matched pair
        action_event_matched = create_autospec(ActionEvent, instance=True)
        action_event_matched.tool_call_id = "call_1"
        action_event_matched.id = "action_1"
        action_event_matched.llm_response_id = "response_1"

        observation_event_matched = create_autospec(ObservationEvent, instance=True)
        observation_event_matched.tool_call_id = "call_1"
        observation_event_matched.id = "obs_1"

        # Unmatched ActionEvent
        action_event_unmatched = create_autospec(ActionEvent, instance=True)
        action_event_unmatched.tool_call_id = "call_2"
        action_event_unmatched.id = "action_2"
        action_event_unmatched.llm_response_id = "response_2"

        events: list[LLMConvertibleEvent] = [
            message,
            action_event_matched,
            observation_event_matched,
            action_event_unmatched,
        ]

        result = self.property.enforce(events, events)

        # Should keep: message, matched pair
        # Should remove: unmatched ActionEvent
        assert result == {action_event_unmatched.id}

    def test_unmatched_observation(self) -> None:
        """Test enforce with unmatched ObservationEvent."""
        message = message_event("Test message")

        # Matched pair
        action_event_matched = create_autospec(ActionEvent, instance=True)
        action_event_matched.tool_call_id = "call_1"
        action_event_matched.id = "action_1"
        action_event_matched.llm_response_id = "response_1"

        observation_event_matched = create_autospec(ObservationEvent, instance=True)
        observation_event_matched.tool_call_id = "call_1"
        observation_event_matched.id = "obs_1"

        # Unmatched ObservationEvent
        observation_event_unmatched = create_autospec(ObservationEvent, instance=True)
        observation_event_unmatched.tool_call_id = "call_2"
        observation_event_unmatched.id = "obs_2"

        events: list[LLMConvertibleEvent] = [
            message,
            action_event_matched,
            observation_event_matched,
            observation_event_unmatched,
        ]

        result = self.property.enforce(events, events)

        # Should keep: message, matched pair
        # Should remove: unmatched ObservationEvent
        assert result == {observation_event_unmatched.id}

    def test_mixed_scenario(self) -> None:
        """Test enforce with complex mixed scenario."""
        message_event_1 = message_event("Message 1")
        message_event_2 = message_event("Message 2")

        # Matched pair 1
        action_event_1 = create_autospec(ActionEvent, instance=True)
        action_event_1.tool_call_id = "call_1"
        action_event_1.id = "action_1"
        action_event_1.llm_response_id = "response_1"

        observation_event_1 = create_autospec(ObservationEvent, instance=True)
        observation_event_1.tool_call_id = "call_1"
        observation_event_1.id = "obs_1"

        # Unmatched ActionEvent
        action_event_unmatched = create_autospec(ActionEvent, instance=True)
        action_event_unmatched.tool_call_id = "call_2"
        action_event_unmatched.id = "action_unmatched"
        action_event_unmatched.llm_response_id = "response_2"

        # Unmatched ObservationEvent
        observation_event_unmatched = create_autospec(ObservationEvent, instance=True)
        observation_event_unmatched.tool_call_id = "call_3"
        observation_event_unmatched.id = "obs_unmatched"

        # Matched pair 2
        action_event_2 = create_autospec(ActionEvent, instance=True)
        action_event_2.tool_call_id = "call_4"
        action_event_2.id = "action_2"
        action_event_2.llm_response_id = "response_3"

        observation_event_2 = create_autospec(ObservationEvent, instance=True)
        observation_event_2.tool_call_id = "call_4"
        observation_event_2.id = "obs_2"

        events: list[LLMConvertibleEvent] = [
            message_event_1,
            action_event_1,
            observation_event_1,
            action_event_unmatched,
            observation_event_unmatched,
            message_event_2,
            action_event_2,
            observation_event_2,
        ]

        result = self.property.enforce(events, events)

        # Should remove: unmatched action and observation events
        assert action_event_unmatched.id in result
        assert observation_event_unmatched.id in result
        assert action_event_1.id not in result
        assert observation_event_1.id not in result
        assert action_event_2.id not in result
        assert observation_event_2.id not in result

    def test_with_user_reject_observation(self) -> None:
        """Test that ActionEvent with UserRejectObservation is not filtered out."""
        action_event = create_autospec(ActionEvent, instance=True)
        action_event.tool_call_id = "call_1"
        action_event.id = "action_1"
        action_event.llm_response_id = "response_1"

        user_reject_obs = UserRejectObservation(
            action_id="action_1",
            tool_name="TerminalTool",
            tool_call_id="call_1",
            rejection_reason="User rejected the action",
        )

        message1 = message_event("First message")
        message2 = message_event("Second message")

        events: list[LLMConvertibleEvent] = [
            message1,
            action_event,
            user_reject_obs,
            message2,
        ]

        result = self.property.enforce(events, events)

        # Both the ActionEvent and UserRejectObservation should be kept
        assert len(result) == 0

    def test_with_agent_error_event(self) -> None:
        """Test that ActionEvent paired with AgentErrorEvent is not filtered out."""
        action_event = create_autospec(ActionEvent, instance=True)
        action_event.tool_call_id = "call_1"
        action_event.id = "action_1"
        action_event.llm_response_id = "response_1"

        agent_error = AgentErrorEvent(
            error="Tool execution failed",
            tool_name="TerminalTool",
            tool_call_id="call_1",
        )

        message1 = message_event("First message")
        message2 = message_event("Second message")

        events: list[LLMConvertibleEvent] = [
            message1,
            action_event,
            agent_error,
            message2,
        ]

        result = self.property.enforce(events, events)

        # Both the ActionEvent and AgentErrorEvent should be kept
        assert len(result) == 0

    def test_mixed_observation_types(self) -> None:
        """Test filtering with mixed observation types."""
        # ActionEvents
        action_event_1 = create_autospec(ActionEvent, instance=True)
        action_event_1.tool_call_id = "call_1"
        action_event_1.id = "action_1"
        action_event_1.llm_response_id = "response_1"

        action_event_2 = create_autospec(ActionEvent, instance=True)
        action_event_2.tool_call_id = "call_2"
        action_event_2.id = "action_2"
        action_event_2.llm_response_id = "response_2"

        action_event_3 = create_autospec(ActionEvent, instance=True)
        action_event_3.tool_call_id = "call_3"
        action_event_3.id = "action_3"
        action_event_3.llm_response_id = "response_3"

        # Normal observation
        observation_event = create_autospec(ObservationEvent, instance=True)
        observation_event.tool_call_id = "call_1"
        observation_event.id = "obs_1"

        # User rejection
        user_reject_obs = UserRejectObservation(
            action_id="action_2",
            tool_name="TerminalTool",
            tool_call_id="call_2",
            rejection_reason="User rejected the action",
        )

        # Agent error
        agent_error = AgentErrorEvent(
            error="Tool execution failed",
            tool_name="TerminalTool",
            tool_call_id="call_3",
        )

        events: list[LLMConvertibleEvent] = [
            message_event("Start"),
            action_event_1,
            observation_event,
            action_event_2,
            user_reject_obs,
            action_event_3,
            agent_error,
            message_event("End"),
        ]

        result = self.property.enforce(events, events)

        # All matched pairs should be kept
        assert len(result) == 0


class TestToolCallMatchingPropertyManipulationIndices(TestToolCallMatchingBase):
    """Tests for the manipulation_indices method of ToolCallMatchingProperty."""

    def test_single_event_complete_indices(self) -> None:
        """Test manipulation indices for a single unpairable event are complete."""
        message = message_event("Test")
        events: list[LLMConvertibleEvent] = [message]

        result = self.property.manipulation_indices(events)

        assert result == ManipulationIndices.complete(events)

    def test_matched_pair_no_index_between(self) -> None:
        """Test no manipulation index between matched action and observation."""
        action = create_autospec(ActionEvent, instance=True)
        action.tool_call_id = "call_1"
        action.id = "action_1"
        action.llm_response_id = "response_1"

        observation = create_autospec(ObservationEvent, instance=True)
        observation.tool_call_id = "call_1"
        observation.id = "obs_1"

        events: list[LLMConvertibleEvent] = [action, observation]

        result = self.property.manipulation_indices(events)

        # Index 1 (between action and observation) should not be allowed
        assert 1 not in result

    def test_allow_index_between_pairs(self) -> None:
        """Test that manipulation is allowed between separate matched pairs."""
        # First pair
        action1 = create_autospec(ActionEvent, instance=True)
        action1.tool_call_id = "call_1"
        action1.id = "action_1"
        action1.llm_response_id = "response_1"

        observation1 = create_autospec(ObservationEvent, instance=True)
        observation1.tool_call_id = "call_1"
        observation1.id = "obs_1"

        # Second pair
        action2 = create_autospec(ActionEvent, instance=True)
        action2.tool_call_id = "call_2"
        action2.id = "action_2"
        action2.llm_response_id = "response_2"

        observation2 = create_autospec(ObservationEvent, instance=True)
        observation2.tool_call_id = "call_2"
        observation2.id = "obs_2"

        events: list[LLMConvertibleEvent] = [
            action1,
            observation1,
            action2,
            observation2,
        ]

        result = self.property.manipulation_indices(events)

        # Index 2 (between the two pairs) should be allowed
        assert 2 in result
        # Index 1 (between action1 and observation1) should not be allowed
        assert 1 not in result

    def test_empty_events(self) -> None:
        """Test manipulation indices for empty events are complete."""
        events: list[LLMConvertibleEvent] = []

        result = self.property.manipulation_indices(events)
        assert result == ManipulationIndices.complete(events)
