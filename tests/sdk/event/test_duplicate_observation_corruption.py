"""Tests for event stream validation and repair.

Tests verify that:
1. Corrupt event streams are detected via validation
2. prepare_events_for_llm() handles all validation issues
3. events_to_messages() automatically validates by default
4. get_repair_events() returns synthetic events for persistence
"""

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import ActionEvent, ObservationEvent
from openhands.sdk.event.validation import (
    get_repair_events,
    prepare_events_for_llm,
    validate_event_stream,
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


class TestPrepareEventsForLlm:
    """Tests for prepare_events_for_llm() - the unified repair function."""

    def test_fixes_orphan_action(self):
        """Adds synthetic observation for orphan action."""
        action = make_action("tc1", tool_name="terminal")
        prepared, mods = prepare_events_for_llm([action])

        assert len(prepared) == 2  # action + synthetic observation
        assert len(mods) == 1
        assert "orphan action" in mods[0].lower()

        # Verify synthetic observation
        from openhands.sdk.event.llm_convertible import AgentErrorEvent

        synthetic = prepared[1]
        assert isinstance(synthetic, AgentErrorEvent)
        assert synthetic.tool_call_id == "tc1"
        assert synthetic.tool_name == "terminal"

    def test_removes_duplicate_observation(self):
        """Removes duplicate observations, keeps first."""
        action = make_action("tc1")
        obs1 = make_observation("tc1", "a_tc1")
        obs2 = make_observation("tc1", "a_tc1")

        prepared, mods = prepare_events_for_llm([action, obs1, obs2])

        assert len(prepared) == 2  # action + first observation only
        assert len(mods) == 1
        assert "duplicate" in mods[0].lower()

    def test_removes_orphan_observation(self):
        """Removes observation without matching action."""
        action = make_action("tc1")
        obs_valid = make_observation("tc1", "a_tc1")
        obs_orphan = make_observation("tc_orphan", "a_unknown")

        prepared, mods = prepare_events_for_llm([action, obs_valid, obs_orphan])

        assert len(prepared) == 2  # action + valid observation only
        assert len(mods) == 1
        assert "orphan observation" in mods[0].lower()

    def test_handles_all_issues_together(self):
        """Handles multiple issues in single call."""
        action1 = make_action("tc1")
        action2 = make_action("tc2")  # orphan - no observation
        obs1 = make_observation("tc1", "a_tc1")
        obs1_dup = make_observation("tc1", "a_tc1")  # duplicate
        obs_orphan = make_observation("tc_orphan", "a_unknown")  # orphan

        prepared, mods = prepare_events_for_llm(
            [action1, action2, obs1, obs1_dup, obs_orphan]
        )

        # Should have: action1, obs1, action2, synthetic_for_tc2
        assert len(mods) == 3  # 1 duplicate + 1 orphan obs + 1 orphan action
        errors = validate_event_stream(prepared)
        assert errors == []  # All fixed

    def test_valid_stream_unchanged(self):
        """Valid stream passes through unchanged."""
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")

        prepared, mods = prepare_events_for_llm([action, obs])

        assert len(prepared) == 2
        assert mods == []


class TestEventsToMessagesValidation:
    """Tests for events_to_messages() with validation."""

    def test_validates_by_default(self):
        """events_to_messages validates by default."""
        action = make_action("tc1")
        # No observation - would cause LLM error without validation

        # Should not raise - validation adds synthetic observation
        messages = LLMConvertibleEvent.events_to_messages([action])
        assert len(messages) == 2  # action + synthetic tool response

    def test_can_disable_validation(self):
        """Can disable validation with validate=False."""
        action = make_action("tc1")
        obs = make_observation("tc1", "a_tc1")

        messages = LLMConvertibleEvent.events_to_messages([action, obs], validate=False)
        assert len(messages) == 2


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
