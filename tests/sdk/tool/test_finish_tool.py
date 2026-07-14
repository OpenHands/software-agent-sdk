"""Tests for FinishAction outcome declaration (success vs. infeasible)."""

import pytest
from pydantic import ValidationError

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import (
    FinishAction,
    FinishExecutor,
    FinishObservation,
    FinishTool,
)


def _make_action_event(action: FinishAction) -> ActionEvent:
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="Finishing the task")],
        action=action,
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )


def test_finish_action_defaults_to_success():
    """Existing behavior is unchanged: message-only actions succeed."""
    action = FinishAction(message="Task completed")
    assert action.outcome == "success"
    assert action.reason is None


def test_finish_action_infeasible_with_reason():
    action = FinishAction(
        message="Cannot complete the task",
        outcome="infeasible",
        reason="The referenced API does not exist in this codebase",
    )
    assert action.outcome == "infeasible"
    assert action.reason == "The referenced API does not exist in this codebase"


def test_finish_action_infeasible_requires_reason():
    with pytest.raises(ValidationError, match="reason is required"):
        FinishAction(message="Cannot do it", outcome="infeasible")


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_finish_action_rejects_empty_reason(reason: str):
    with pytest.raises(ValidationError, match="non-empty"):
        FinishAction(message="Cannot do it", outcome="infeasible", reason=reason)


@pytest.mark.parametrize("reason", ["", "   "])
def test_finish_action_rejects_empty_reason_even_on_success(reason: str):
    with pytest.raises(ValidationError, match="non-empty"):
        FinishAction(message="Done", outcome="success", reason=reason)


def test_finish_action_rejects_unknown_outcome():
    with pytest.raises(ValidationError):
        FinishAction(message="Done", outcome="partial")  # type: ignore[arg-type]


def test_outcome_detectable_from_action_event_without_text_parsing():
    """Orchestrators can read the verdict off the ActionEvent directly."""
    event = _make_action_event(
        FinishAction(
            message="This task cannot be completed.",
            outcome="infeasible",
            reason="Required credentials are not available",
        )
    )
    assert isinstance(event.action, FinishAction)
    assert event.action.outcome == "infeasible"
    assert event.action.reason == "Required credentials are not available"


def test_executor_returns_observation_for_infeasible_outcome():
    executor = FinishExecutor()
    observation = executor(
        FinishAction(
            message="Cannot proceed",
            outcome="infeasible",
            reason="Feature depends on an unreachable service",
        )
    )
    assert isinstance(observation, FinishObservation)


def test_finish_action_serialization_roundtrip():
    action = FinishAction(
        message="Cannot proceed",
        outcome="infeasible",
        reason="Repository is read-only",
    )
    restored = FinishAction.model_validate(action.model_dump())
    assert restored == action


def test_tool_schema_exposes_outcome_and_reason():
    (tool,) = FinishTool.create()
    schema = tool.action_type.model_json_schema()
    assert "outcome" in schema["properties"]
    assert "reason" in schema["properties"]
    # message stays the only required field for backward compatibility
    assert schema.get("required", []) == ["message"]


def test_infeasible_visualization_mentions_reason():
    action = FinishAction(
        message="Cannot proceed",
        outcome="infeasible",
        reason="Repository is read-only",
    )
    text = action.visualize.plain
    assert "infeasible" in text
    assert "Repository is read-only" in text
