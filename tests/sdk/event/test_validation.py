"""Tests for event stream validation and repair.

Tests verify that:
1. Corrupt event streams are detected via validate_event_stream()
2. validate_for_llm() raises clear errors for invalid streams
3. get_repair_events() returns synthetic events for persistence on resume
4. Integration: prepare_llm_messages() validates before conversion
"""

import pytest

from openhands.sdk.event.llm_convertible import ActionEvent, ObservationEvent
from openhands.sdk.event.validation import (
    EventStreamValidationError,
    _index_tool_calls,
    get_repair_events,
    validate_event_stream,
    validate_for_llm,
)
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def make_action(tool_call_id: str, tool_name: str = "x", response_id: str = "r1"):
    """Helper to create ActionEvent."""
    return ActionEvent(
        id=f"a_{tool_call_id}",
        thought=[TextContent(text="t")],
        action=MCPToolAction(data={}),
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id, name=tool_name, arguments="{}", origin="completion"
        ),
        llm_response_id=response_id,
        source="agent",
    )


def make_observation(tool_call_id: str, action_id: str, tool_name: str = "x"):
    """Helper to create ObservationEvent."""
    return ObservationEvent(
        id=f"o_{tool_call_id}",
        observation=MCPToolObservation.from_text("result", tool_name=tool_name),
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id=action_id,
        source="environment",
    )


class TestIndexToolCalls:
    """Tests for _index_tool_calls() helper."""

    def test_indexes_actions_and_observations(self):
        """Correctly indexes tool_call_ids."""
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")

        action_map, obs_ids = _index_tool_calls([action, obs])

        assert "tc1" in action_map
        assert action_map["tc1"] == action
        assert obs_ids == {"tc1"}


class TestValidateEventStream:
    """Tests for validate_event_stream() detection."""

    def test_valid_stream_returns_no_errors(self):
        """Valid event stream returns empty error list."""
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")
        errors = validate_event_stream([action, obs])
        assert errors == []

    def test_detects_orphan_action(self):
        """Detects action without observation."""
        action = make_action("tc1")
        errors = validate_event_stream([action])
        assert len(errors) == 1
        assert "Orphan action" in errors[0]

    def test_detects_duplicate_observation(self):
        """Detects duplicate observations for same tool_call_id."""
        action = make_action("tc1")
        obs1 = make_observation("tc1", "a_tc1")
        obs2 = make_observation("tc1", "a_tc1")
        errors = validate_event_stream([action, obs1, obs2])
        assert len(errors) == 1
        assert "Duplicate" in errors[0]

    def test_detects_orphan_observation(self):
        """Detects observation without matching action."""
        obs = make_observation("tc_orphan", "a_unknown")
        errors = validate_event_stream([obs])
        assert len(errors) == 1
        assert "Orphan observation" in errors[0]


class TestValidateForLlm:
    """Tests for validate_for_llm() - raises clear errors."""

    def test_valid_stream_passes(self):
        """Valid stream does not raise."""
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")
        validate_for_llm([action, obs])  # Should not raise

    def test_raises_on_orphan_action(self):
        """Raises clear error for orphan action."""
        action = make_action("tc1")
        with pytest.raises(EventStreamValidationError) as exc_info:
            validate_for_llm([action])
        assert "Orphan action" in str(exc_info.value)
        assert "tc1" in str(exc_info.value)
        assert exc_info.value.errors == ["Orphan action (no observation): tc1"]

    def test_raises_on_duplicate_observation(self):
        """Raises clear error for duplicate observation."""
        action = make_action("tc1")
        obs1 = make_observation("tc1", "a_tc1")
        obs2 = make_observation("tc1", "a_tc1")
        with pytest.raises(EventStreamValidationError) as exc_info:
            validate_for_llm([action, obs1, obs2])
        assert "Duplicate" in str(exc_info.value)

    def test_error_includes_all_issues(self):
        """Error message includes all issues found."""
        action = make_action("tc1")
        obs_orphan = make_observation("tc_orphan", "a_unknown")

        with pytest.raises(EventStreamValidationError) as exc_info:
            validate_for_llm([action, obs_orphan])

        error_msg = str(exc_info.value)
        assert "Orphan action" in error_msg
        assert "Orphan observation" in error_msg


class TestGetRepairEvents:
    """Tests for get_repair_events() - for persistence on resume."""

    def test_returns_synthetic_for_orphan_action(self):
        """Returns synthetic AgentErrorEvent for orphan action."""
        action = make_action("tc1", tool_name="terminal")
        repair_events = get_repair_events([action])

        assert len(repair_events) == 1
        assert repair_events[0].tool_call_id == "tc1"
        assert repair_events[0].tool_name == "terminal"
        assert "interrupted" in repair_events[0].error.lower()

    def test_returns_empty_for_valid_stream(self):
        """Returns empty list for valid event stream."""
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")

        repair_events = get_repair_events([action, obs])
        assert repair_events == []

    def test_handles_multiple_orphan_actions(self):
        """Returns synthetic events for all orphan actions."""
        action1 = make_action("tc1")
        action2 = make_action("tc2")

        repair_events = get_repair_events([action1, action2])

        assert len(repair_events) == 2
        tool_call_ids = {e.tool_call_id for e in repair_events}
        assert tool_call_ids == {"tc1", "tc2"}

    def test_does_not_repair_duplicates(self):
        """Does NOT return repairs for duplicates (requires investigation)."""
        action = make_action("tc1")
        obs1 = make_observation("tc1", "a_tc1")
        obs2 = make_observation("tc1", "a_tc1")

        repair_events = get_repair_events([action, obs1, obs2])
        assert repair_events == []


class TestPrepareLlmMessagesIntegration:
    """Tests for prepare_llm_messages() validation integration."""

    def test_raises_on_invalid_event_stream(self):
        """prepare_llm_messages raises when event stream is invalid."""
        from openhands.sdk.agent.utils import prepare_llm_messages

        action = make_action("tc1")
        # No observation - orphan action

        with pytest.raises(EventStreamValidationError) as exc_info:
            prepare_llm_messages([action])

        assert "Orphan action" in str(exc_info.value)

    def test_passes_with_valid_event_stream(self):
        """prepare_llm_messages works with valid event stream."""
        from openhands.sdk.agent.utils import prepare_llm_messages

        # Valid event stream: action with matching observation
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")

        messages = prepare_llm_messages([action, obs])
        assert len(messages) == 2  # action message + tool response
