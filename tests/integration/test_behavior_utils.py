"""Tests for behavior_utils functions."""

from collections.abc import Sequence

from openhands.sdk.event import ActionEvent
from openhands.sdk.event.base import Event
from openhands.sdk.llm.message import MessageToolCall, TextContent
from tests.integration.behavior_utils import verify_all_actions_have_summary


def _create_action_event(tool_name: str, summary: str | None) -> ActionEvent:
    """Helper to create an ActionEvent with a given summary."""
    return ActionEvent(
        source="agent",
        tool_name=tool_name,
        thought=[TextContent(text="test thought")],
        tool_call_id="test-call-id",
        tool_call=MessageToolCall(
            id="test-id",
            name=tool_name,
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="test-response-id",
        summary=summary,
    )


def test_verify_all_actions_have_summary_all_present():
    """Test that verification passes when all actions have summaries."""
    events: Sequence[Event] = [
        _create_action_event("terminal", "running tests"),
        _create_action_event("file_editor", "editing config file"),
    ]
    success, reason = verify_all_actions_have_summary(list(events))
    assert success is True
    assert "All 2 actions have summaries" in reason


def test_verify_all_actions_have_summary_missing():
    """Test that verification fails when an action is missing a summary."""
    events: Sequence[Event] = [
        _create_action_event("terminal", "running tests"),
        _create_action_event("file_editor", None),
    ]
    success, reason = verify_all_actions_have_summary(list(events))
    assert success is False
    assert "file_editor" in reason


def test_verify_all_actions_have_summary_empty_string():
    """Test that verification fails when summary is empty string."""
    events: Sequence[Event] = [
        _create_action_event("terminal", ""),
    ]
    success, reason = verify_all_actions_have_summary(list(events))
    assert success is False
    assert "terminal" in reason


def test_verify_all_actions_have_summary_whitespace_only():
    """Test that verification fails when summary is whitespace only."""
    events: Sequence[Event] = [
        _create_action_event("terminal", "   "),
    ]
    success, reason = verify_all_actions_have_summary(list(events))
    assert success is False
    assert "terminal" in reason


def test_verify_all_actions_have_summary_no_actions():
    """Test that verification passes when there are no action events."""
    events: list[Event] = []
    success, reason = verify_all_actions_have_summary(events)
    assert success is True
    assert "No action events found" in reason
