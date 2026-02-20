"""Tests for event stream validation and repair.

Tests verify that corrupt event streams are detected and repaired
before conversion to LLM messages.
"""

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    ObservationEvent,
)
from openhands.sdk.event.validation import (
    repair_event_stream,
    validate_event_stream,
)
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


class TestEventStreamValidation:
    """Tests for validate_event_stream()."""

    def test_valid_stream_returns_no_errors(self):
        """Valid event stream returns empty error list."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("result", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        errors = validate_event_stream([action, obs])
        assert errors == []

    def test_detects_orphan_action(self):
        """Detects action without observation."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        errors = validate_event_stream([action])
        assert len(errors) == 1
        assert "Orphan action" in errors[0]

    def test_detects_duplicate_observation(self):
        """Detects duplicate observations for same tool_call_id."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("a", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text("b", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        errors = validate_event_stream([action, obs1, obs2])
        assert len(errors) == 1
        assert "Duplicate" in errors[0]


class TestEventStreamRepair:
    """Tests for repair_event_stream()."""

    def test_adds_synthetic_observation_for_orphan(self):
        """Adds synthetic error observation for orphan action."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )

        repaired, repairs = repair_event_stream([action])

        assert len(repairs) == 1
        assert "synthetic" in repairs[0].lower()
        assert len(repaired) == 2
        assert validate_event_stream(repaired) == []

    def test_removes_duplicate_observations(self):
        """Removes duplicate observations, keeps first."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="x",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("first", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text("dup", tool_name="x"),
            tool_name="x",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )

        repaired, repairs = repair_event_stream([action, obs1, obs2])

        assert len(repairs) == 1
        assert "duplicate" in repairs[0].lower()
        assert len(repaired) == 2
        assert validate_event_stream(repaired) == []


class TestEventsToMessagesRepair:
    """Tests that events_to_messages() auto-repairs."""

    def test_repairs_orphan_action(self):
        """Orphan action gets synthetic observation."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )

        messages = LLMConvertibleEvent.events_to_messages([action])
        tool_results = [m for m in messages if m.role == "tool"]
        assert len(tool_results) == 1

    def test_repairs_duplicate_observations(self):
        """Duplicate observations are deduplicated."""
        action = ActionEvent(
            id="a1",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        obs1 = ObservationEvent(
            id="o1",
            observation=MCPToolObservation.from_text("first", tool_name="terminal"),
            tool_name="terminal",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )
        obs2 = ObservationEvent(
            id="o2",
            observation=MCPToolObservation.from_text("dup", tool_name="terminal"),
            tool_name="terminal",
            tool_call_id="tc1",
            action_id="a1",
            source="environment",
        )

        messages = LLMConvertibleEvent.events_to_messages([action, obs1, obs2])
        tool_results = [m for m in messages if m.role == "tool"]
        assert len(tool_results) == 1
