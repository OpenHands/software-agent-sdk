"""Tests for event stream validation and repair on conversation resume.

Tests verify that:
1. Corrupt event streams are detected via validation
2. get_repair_events() returns synthetic events to fix orphan actions
3. ConversationState.repair_event_stream() integrates the repair
"""

from openhands.sdk.event.llm_convertible import ActionEvent, ObservationEvent
from openhands.sdk.event.validation import get_repair_events, validate_event_stream
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


class TestGetRepairEvents:
    """Tests for get_repair_events()."""

    def test_returns_synthetic_for_orphan_action(self):
        """Returns synthetic AgentErrorEvent for orphan action."""
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

        repair_events = get_repair_events([action])

        assert len(repair_events) == 1
        assert repair_events[0].tool_call_id == "tc1"
        assert repair_events[0].tool_name == "terminal"
        assert "interrupted" in repair_events[0].error.lower()

    def test_returns_empty_for_valid_stream(self):
        """Returns empty list for valid event stream."""
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

        repair_events = get_repair_events([action, obs])
        assert repair_events == []

    def test_handles_multiple_orphan_actions(self):
        """Returns synthetic events for all orphan actions."""
        action1 = ActionEvent(
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
        action2 = ActionEvent(
            id="a2",
            thought=[TextContent(text="t")],
            action=MCPToolAction(data={}),
            tool_name="y",
            tool_call_id="tc2",
            tool_call=MessageToolCall(
                id="tc2", name="y", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )

        repair_events = get_repair_events([action1, action2])

        assert len(repair_events) == 2
        tool_call_ids = {e.tool_call_id for e in repair_events}
        assert tool_call_ids == {"tc1", "tc2"}
