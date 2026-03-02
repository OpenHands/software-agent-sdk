"""Tests for ToolResultUniquenessProperty.

This module tests that duplicate tool results for the same tool_call_id
are properly handled:
- AgentErrorEvent content is merged into ObservationEvent when both exist
- Duplicates are removed after merging
- ONLY consecutive duplicates are handled; non-consecutive ones are NOT
"""

from openhands.sdk.context.view.properties.tool_result_uniqueness import (
    ToolResultUniquenessProperty,
    _create_merged_observation,
    _group_consecutive_observations_by_tool_call,
)
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import TextContent
from openhands.sdk.tool.schema import Observation
from tests.sdk.context.view.properties.conftest import message_event


class ToolResultUniquenessTestObservation(Observation):
    """Simple observation for testing tool result uniqueness."""

    pass


def create_observation_event(
    event_id: str,
    tool_call_id: str,
    content: str,
    tool_name: str = "terminal",
    action_id: str = "action_1",
) -> ObservationEvent:
    """Helper to create real ObservationEvent instances for testing."""
    return ObservationEvent(
        id=event_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        observation=ToolResultUniquenessTestObservation(
            content=[TextContent(text=content)]
        ),
        action_id=action_id,
    )


class TestGroupConsecutiveObservations:
    """Tests for the _group_consecutive_observations_by_tool_call helper."""

    def test_no_observations(self) -> None:
        """Test with no observations returns empty dict."""
        events: list[LLMConvertibleEvent] = [message_event("Hello")]
        result = _group_consecutive_observations_by_tool_call(events)
        assert result == {}

    def test_single_observation_no_duplicates(self) -> None:
        """Test that single observations are not grouped."""
        obs = create_observation_event("obs_1", "call_1", "Result")
        events: list[LLMConvertibleEvent] = [obs]
        result = _group_consecutive_observations_by_tool_call(events)
        assert result == {}

    def test_consecutive_duplicates_are_grouped(self) -> None:
        """Test that consecutive observations with same tool_call_id are grouped."""
        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart error",
        )
        obs = create_observation_event("obs_1", "call_1", "Result")
        events: list[LLMConvertibleEvent] = [error, obs]

        result = _group_consecutive_observations_by_tool_call(events)

        assert "call_1" in result
        assert len(result["call_1"]) == 2
        assert error in result["call_1"]
        assert obs in result["call_1"]

    def test_non_consecutive_duplicates_are_not_grouped(self) -> None:
        """Test non-consecutive observations with same tool_call_id are NOT grouped."""
        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart error",
        )
        obs = create_observation_event("obs_1", "call_1", "Result")
        # A message event between them breaks consecutiveness
        events: list[LLMConvertibleEvent] = [error, message_event("Interruption"), obs]

        result = _group_consecutive_observations_by_tool_call(events)

        # Should be empty - non-consecutive duplicates are not handled
        assert result == {}

    def test_multiple_consecutive_groups(self) -> None:
        """Test multiple groups of consecutive duplicates."""
        # Group 1
        error1 = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Error 1",
        )
        obs1 = create_observation_event("obs_1", "call_1", "Result 1")
        # Group 2 (after a message)
        error2 = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_2",
            error="Error 2",
        )
        obs2 = create_observation_event("obs_2", "call_2", "Result 2")

        events: list[LLMConvertibleEvent] = [
            error1,
            obs1,
            message_event("Between groups"),
            error2,
            obs2,
        ]

        result = _group_consecutive_observations_by_tool_call(events)

        assert "call_1" in result
        assert "call_2" in result
        assert len(result["call_1"]) == 2
        assert len(result["call_2"]) == 2


class TestCreateMergedObservation:
    """Tests for the _create_merged_observation helper function."""

    def test_merges_single_error_into_observation(self) -> None:
        """Test that a single AgentErrorEvent is merged into ObservationEvent."""
        obs = create_observation_event("obs_1", "call_1", "Actual result")
        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart occurred",
        )

        merged = _create_merged_observation(obs, [error])

        # ID format: {original_id}-merged-{uuid}
        assert merged.id.startswith("obs_1-merged-")
        assert merged.tool_call_id == "call_1"
        assert merged.tool_name == "terminal"
        # Check that error context is prepended
        content_text = "".join(
            c.text for c in merged.observation.content if isinstance(c, TextContent)
        )
        assert "[Note: Restart occurred]" in content_text
        assert "Actual result" in content_text

    def test_merges_multiple_errors_into_observation(self) -> None:
        """Test that multiple AgentErrorEvents are merged into ObservationEvent."""
        obs = create_observation_event("obs_1", "call_1", "Actual result")
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

        merged = _create_merged_observation(obs, [error1, error2])

        content_text = "".join(
            c.text for c in merged.observation.content if isinstance(c, TextContent)
        )
        assert "[Note: First error]" in content_text
        assert "[Note: Second error]" in content_text
        assert "Actual result" in content_text


class TestToolResultUniquenessPropertyTransform:
    """Tests for the transform method of ToolResultUniquenessProperty."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolResultUniquenessProperty()

    def test_no_transform_when_no_duplicates(self) -> None:
        """Test that no transforms occur when there are no duplicates."""
        obs = create_observation_event("obs_1", "call_1", "Result")
        events: list[LLMConvertibleEvent] = [obs]

        transforms = self.property.transform(events, events)

        assert transforms == {}

    def test_transforms_when_consecutive_error_and_observation_exist(self) -> None:
        """Test ObservationEvent transforms when consecutive AgentErrorEvent exists."""
        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart occurred",
        )
        obs = create_observation_event("obs_1", "call_1", "Actual result")
        # Error and obs are consecutive
        events: list[LLMConvertibleEvent] = [error, obs]

        transforms = self.property.transform(events, events)

        # Original obs should be transformed
        assert "obs_1" in transforms
        merged = transforms["obs_1"]
        assert isinstance(merged, ObservationEvent)
        # ID format: {original_id}-merged-{uuid}
        assert merged.id.startswith("obs_1-merged-")
        # Error content should be in merged observation
        content_text = "".join(
            c.text
            for c in merged.observation.content  # type: ignore[union-attr]
            if isinstance(c, TextContent)
        )
        assert "[Note: Restart occurred]" in content_text
        assert "Actual result" in content_text

    def test_no_transform_when_non_consecutive_duplicates(self) -> None:
        """Test that non-consecutive duplicates are NOT transformed."""
        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart occurred",
        )
        obs = create_observation_event("obs_1", "call_1", "Actual result")
        # A message between them breaks consecutiveness
        events: list[LLMConvertibleEvent] = [error, message_event("Interruption"), obs]

        transforms = self.property.transform(events, events)

        # No transform - non-consecutive duplicates should fail at API level
        assert transforms == {}

    def test_no_transform_when_only_errors(self) -> None:
        """Test that no transform occurs when only AgentErrorEvents exist."""
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

        transforms = self.property.transform(events, events)

        # No transforms - just enforcement will keep the last error
        assert transforms == {}


class TestToolResultUniquenessPropertyEnforcement:
    """Tests for the enforce method of ToolResultUniquenessProperty.

    NOTE: enforce() only handles CONSECUTIVE duplicates. Non-consecutive
    duplicates are intentionally left untouched to expose bugs.
    """

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolResultUniquenessProperty()

    def test_empty_list(self) -> None:
        """Test enforce with empty event list."""
        result = self.property.enforce([], [])
        assert result == set()

    def test_no_duplicates(self) -> None:
        """Test enforce when there are no duplicate tool_call_ids."""
        obs1 = create_observation_event("obs_1", "call_1", "Result 1")
        obs2 = create_observation_event("obs_2", "call_2", "Result 2")

        events: list[LLMConvertibleEvent] = [
            message_event("Start"),
            obs1,
            message_event("Middle"),
            obs2,
            message_event("End"),
        ]

        result = self.property.enforce(events, events)
        assert result == set()

    def test_consecutive_duplicate_observation_events(self) -> None:
        """Test that consecutive duplicate ObservationEvents keep the later one."""
        obs1 = create_observation_event("obs_1", "call_1", "First result")
        obs2 = create_observation_event("obs_2", "call_1", "Second result")
        # Consecutive duplicates
        events: list[LLMConvertibleEvent] = [obs1, obs2]

        result = self.property.enforce(events, events)
        # obs1 should be removed, obs2 (later) should be kept
        assert result == {"obs_1"}

    def test_non_consecutive_duplicates_not_enforced(self) -> None:
        """Test that non-consecutive duplicates are NOT removed."""
        obs1 = create_observation_event("obs_1", "call_1", "First result")
        obs2 = create_observation_event("obs_2", "call_1", "Second result")
        # Message between them breaks consecutiveness
        events: list[LLMConvertibleEvent] = [obs1, message_event("Interruption"), obs2]

        result = self.property.enforce(events, events)
        # Nothing removed - non-consecutive duplicates should fail at API level
        assert result == set()

    def test_consecutive_observation_event_preferred_over_agent_error(self) -> None:
        """Test ObservationEvent is preferred over AgentErrorEvent when consecutive."""
        agent_error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart occurred while tool was running",
        )
        obs = create_observation_event("obs_1", "call_1", "Actual result")
        # Consecutive
        events: list[LLMConvertibleEvent] = [agent_error, obs]

        result = self.property.enforce(events, events)
        # AgentErrorEvent should be removed, ObservationEvent kept
        assert result == {agent_error.id}

    def test_consecutive_agent_error_before_observation_event(self) -> None:
        """Test AgentErrorEvent followed by ObservationEvent (restart scenario)."""
        agent_error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="A restart occurred while this tool was in progress.",
        )
        obs = create_observation_event("obs_1", "call_1", "Actual result")
        # Consecutive after the message event
        events: list[LLMConvertibleEvent] = [
            message_event("User message"),
            agent_error,
            obs,  # Actual result arrives immediately after error (consecutive)
        ]

        result = self.property.enforce(events, events)
        # AgentErrorEvent should be removed since we have actual ObservationEvent
        assert result == {agent_error.id}

    def test_consecutive_multiple_agent_errors_keep_last(self) -> None:
        """Test when only consecutive AgentErrorEvents exist, the last one is kept."""
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
        # Consecutive
        events: list[LLMConvertibleEvent] = [error1, error2]

        result = self.property.enforce(events, events)
        # First error should be removed, second (later) should be kept
        assert result == {error1.id}

    def test_consecutive_user_reject_observation_handling(self) -> None:
        """Test that UserRejectObservation is handled correctly when consecutive."""
        reject = UserRejectObservation(
            tool_name="terminal",
            tool_call_id="call_1",
            action_id="action_1",
            rejection_reason="User rejected",
        )
        obs = create_observation_event("obs_1", "call_1", "Actual result")
        # Consecutive
        events: list[LLMConvertibleEvent] = [reject, obs]

        result = self.property.enforce(events, events)
        # ObservationEvent is preferred over UserRejectObservation
        assert result == {reject.id}

    def test_mixed_scenario_consecutive_duplicates_only(self) -> None:
        """Test with multiple tool calls, only consecutive duplicates handled."""
        # Tool call 1: has consecutive duplicate (error + observation)
        error1 = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="Restart error",
        )
        obs1 = create_observation_event("obs_1", "call_1", "Result 1")

        # Tool call 2: single observation (no duplicate)
        obs2 = create_observation_event("obs_2", "call_2", "Result 2")

        # Tool call 3: single error (no duplicate)
        error3 = AgentErrorEvent(
            tool_name="file_editor",
            tool_call_id="call_3",
            error="Tool not found",
        )

        # error1 and obs1 are consecutive
        events: list[LLMConvertibleEvent] = [
            message_event("Start"),
            error1,
            obs1,  # Consecutive with error1
            message_event("Middle"),
            obs2,
            message_event("Another"),
            error3,
        ]

        result = self.property.enforce(events, events)
        # Only error1 should be removed (consecutive duplicate with obs1)
        assert result == {error1.id}


class TestToolResultUniquenessPropertyManipulationIndices:
    """Tests for the manipulation_indices method."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolResultUniquenessProperty()

    def test_complete_indices_returned(self) -> None:
        """Test that manipulation indices are complete (no restrictions)."""
        obs = create_observation_event("obs_1", "call_1", "Result")

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


class TestToolResultUniquenessEndToEnd:
    """End-to-end tests for the full transform + enforce workflow.

    NOTE: Only CONSECUTIVE duplicates are handled. Non-consecutive duplicates
    are intentionally not processed to expose underlying bugs.
    """

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.property = ToolResultUniquenessProperty()

    def test_restart_scenario_consecutive_merges_and_removes(self) -> None:
        """Test restart scenario: error then actual result arrives consecutively."""
        # Simulates the actual bug scenario:
        # 1. Agent invokes tool
        # 2. Runtime restarts, creates AgentErrorEvent
        # 3. Tool completes immediately, creates ObservationEvent (consecutive)
        # 4. Both events have same tool_call_id

        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="A restart occurred while this tool was in progress.",
        )
        obs = create_observation_event("obs_1", "call_1", "Command output: success")
        # Events are consecutive
        events: list[LLMConvertibleEvent] = [error, obs]

        # Step 1: Transform merges error into observation
        transforms = self.property.transform(events, events)
        assert "obs_1" in transforms
        merged_obs = transforms["obs_1"]

        # Step 2: Apply transforms (simulating what View.enforce_properties does)
        transformed_events = [transforms.get(e.id, e) for e in events]

        # Step 3: Enforce removes the AgentErrorEvent
        to_remove = self.property.enforce(transformed_events, events)
        assert error.id in to_remove

        # Verify final state: only merged observation remains
        final_events = [e for e in transformed_events if e.id not in to_remove]
        assert len(final_events) == 1
        assert final_events[0] == merged_obs

        # Verify merged content contains both error context and actual result
        assert isinstance(merged_obs, ObservationEvent)
        content_text = "".join(
            c.text for c in merged_obs.observation.content if isinstance(c, TextContent)
        )
        assert "restart occurred" in content_text.lower()
        assert "Command output: success" in content_text

    def test_non_consecutive_duplicates_not_handled(self) -> None:
        """Test that non-consecutive duplicates are NOT handled (to expose bugs)."""
        # This scenario represents a bug: duplicates with other events between them
        error = AgentErrorEvent(
            tool_name="terminal",
            tool_call_id="call_1",
            error="A restart occurred while this tool was in progress.",
        )
        obs = create_observation_event("obs_1", "call_1", "Command output: success")
        # Events are NOT consecutive - there's a message between them
        events: list[LLMConvertibleEvent] = [error, message_event("Interruption"), obs]

        # Step 1: Transform should NOT merge non-consecutive events
        transforms = self.property.transform(events, events)
        assert transforms == {}

        # Step 2: Enforce should NOT remove non-consecutive duplicates
        to_remove = self.property.enforce(events, events)
        assert to_remove == set()

        # Both events remain - the API will reject this, exposing the bug
