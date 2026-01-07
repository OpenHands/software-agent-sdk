"""Tests for ActionEvent summary field visualization."""

import pytest

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.risk import SecurityRisk


@pytest.fixture
def tool_call():
    return MessageToolCall(
        id="123", name="test_tool", arguments='{"x": 1}', origin="completion"
    )


def test_action_event_summary_visualization(tool_call):
    """Test that summary appears in visualization when present."""
    event = ActionEvent(
        source="agent",
        thought=[TextContent(text="I need to test")],
        tool_call=tool_call,
        tool_name="test_tool",
        tool_call_id="123",
        llm_response_id="llm-123",
        action=None,
        summary="checking system status",
        security_risk=SecurityRisk.LOW,
    )

    visualization = event.visualize
    assert "checking system status" in visualization
    assert "Summary:" in visualization


def test_action_event_no_summary_visualization(tool_call):
    """Test that visualization works without summary."""
    event = ActionEvent(
        source="agent",
        thought=[TextContent(text="I need to test")],
        tool_call=tool_call,
        tool_name="test_tool",
        tool_call_id="123",
        llm_response_id="llm-123",
        action=None,
        security_risk=SecurityRisk.LOW,
    )

    visualization = event.visualize
    assert "Summary:" not in visualization
