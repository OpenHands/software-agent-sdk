"""Tests for event stream validation and repair.

Tests verify that:
1. validate_for_llm() raises clear errors for invalid streams
2. get_repair_events() returns synthetic events for persistence on resume
3. Integration: prepare_llm_messages() validates before conversion
"""

import pytest

from openhands.sdk.event.llm_convertible import ActionEvent, ObservationEvent
from openhands.sdk.event.validation import (
    EventStreamValidationError,
    get_repair_events,
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


class TestBugRegressions:
    """Regression tests for fixed SDK bugs.

    These tests verify that previously-known bugs are now handled:
    - Bug #2127: Session crash mid-tool-call leaves orphan action
    - Bug #1782: Resume re-executes action creating duplicate observation
    """

    def test_bug_2127_orphan_action_repaired_on_resume(self):
        """Bug #2127: Session crashes mid-tool-call, orphan action is repaired.

        Scenario:
        1. LLM returns tool_call, ActionEvent created
        2. Pod crashes BEFORE tool completes
        3. Observation never created
        4. User resumes -> get_repair_events() creates synthetic observation
        5. validate_for_llm() passes after repair
        """
        # Simulate: action exists but no observation (session crashed)
        action = make_action("call_crash", tool_name="terminal")
        events = [action]

        # On resume, get_repair_events creates synthetic observation
        repairs = get_repair_events(events)
        assert len(repairs) == 1
        assert repairs[0].tool_call_id == "call_crash"
        assert "interrupted" in repairs[0].error.lower()

        # After adding repair events, validation passes
        events_after_repair = list(events) + repairs
        validate_for_llm(events_after_repair)  # Should not raise

    def test_bug_1782_duplicate_observation_detected(self):
        """Bug #1782: Resume re-executes action, duplicate observation detected.

        Scenario:
        1. Action executes, observation created
        2. Pod terminates before checkpoint
        3. Resume re-executes action (missing observation in checkpoint)
        4. Duplicate observation with same tool_call_id
        5. validate_for_llm() raises clear error
        """
        action = make_action("call_dup")
        obs1 = make_observation("call_dup", "a_call_dup")
        obs2 = make_observation("call_dup", "a_call_dup")  # Duplicate!
        events = [action, obs1, obs2]

        # validate_for_llm detects the duplicate
        with pytest.raises(EventStreamValidationError) as exc_info:
            validate_for_llm(events)

        assert "Duplicate observation" in str(exc_info.value)
        assert "call_dup" in str(exc_info.value)
