"""Tests for ActionEvent summary field.

This module tests the summary field functionality in ActionEvent including
serialization and visualization.
"""

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.risk import SecurityRisk


def test_action_event_with_summary():
    """Test creating ActionEvent with summary."""
    tool_call = MessageToolCall(
        id="123", name="test_tool", arguments='{"x": 1}', origin="completion"
    )

    event = ActionEvent(
        source="agent",
        thought=[TextContent(text="I need to test")],
        tool_call=tool_call,
        tool_name="test_tool",
        tool_call_id="123",
        llm_response_id="llm-123",
        action=None,
        summary="testing file system operations",
    )

    assert event.summary == "testing file system operations"


def test_action_event_without_summary():
    """Test creating ActionEvent without summary (None)."""
    tool_call = MessageToolCall(
        id="123", name="test_tool", arguments='{"x": 1}', origin="completion"
    )

    event = ActionEvent(
        source="agent",
        thought=[TextContent(text="I need to test")],
        tool_call=tool_call,
        tool_name="test_tool",
        tool_call_id="123",
        llm_response_id="llm-123",
        action=None,
    )

    assert event.summary is None


def test_action_event_summary_serialization():
    """Test that summary field is properly serialized."""
    tool_call = MessageToolCall(
        id="123", name="test_tool", arguments='{"x": 1}', origin="completion"
    )

    event = ActionEvent(
        source="agent",
        thought=[TextContent(text="I need to test")],
        tool_call=tool_call,
        tool_name="test_tool",
        tool_call_id="123",
        llm_response_id="llm-123",
        action=None,
        summary="reading log files",
    )

    # Serialize to dict
    event_dict = event.model_dump()
    assert "summary" in event_dict
    assert event_dict["summary"] == "reading log files"

    # Deserialize from dict
    restored_event = ActionEvent.model_validate(event_dict)
    assert restored_event.summary == "reading log files"


def test_action_event_summary_visualization():
    """Test that summary appears in visualization."""
    tool_call = MessageToolCall(
        id="123", name="test_tool", arguments='{"x": 1}', origin="completion"
    )

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


def test_action_event_no_summary_visualization():
    """Test that visualization works without summary."""
    tool_call = MessageToolCall(
        id="123", name="test_tool", arguments='{"x": 1}', origin="completion"
    )

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
