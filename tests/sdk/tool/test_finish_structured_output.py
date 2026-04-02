"""Tests for FinishTool structured output functionality."""

import pytest
from pydantic import BaseModel, Field

from openhands.sdk.conversation.response_utils import get_structured_response
from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import (
    FinishAction,
    FinishObservation,
    FinishTool,
    create_structured_finish_action,
)
from openhands.sdk.tool.schema import Action


class CodeReviewResult(BaseModel):
    """Sample structured output schema for testing."""

    score: int = Field(description="Score from 1-10")
    issues_found: list[str] = Field(description="List of issues identified")
    can_merge: bool = Field(description="Whether the code is ready to merge")


class SimpleResult(BaseModel):
    """Simple schema with minimal fields."""

    success: bool = Field(description="Whether the task succeeded")
    summary: str = Field(description="Summary of the result")


def test_finish_tool_without_response_schema():
    """Test that FinishTool without response_schema works as before."""
    tools = FinishTool.create()
    assert len(tools) == 1
    tool = tools[0]

    assert tool.action_type == FinishAction
    assert tool.response_schema is None
    assert "Signals the completion" in tool.description
    assert "schema" not in tool.description.lower()


def test_finish_tool_with_response_schema():
    """Test that FinishTool with response_schema creates structured action."""
    tools = FinishTool.create(response_schema=CodeReviewResult)
    assert len(tools) == 1
    tool = tools[0]

    # Should have a dynamic action type
    assert tool.action_type != FinishAction
    assert "StructuredCodeReviewResultFinishAction" in tool.action_type.__name__
    assert tool.response_schema == CodeReviewResult

    # Description should include schema info
    assert "schema" in tool.description.lower()
    assert "score" in tool.description
    assert "issues_found" in tool.description
    assert "can_merge" in tool.description


def test_finish_tool_rejects_unknown_params():
    """Test that FinishTool raises error for unknown parameters."""
    with pytest.raises(ValueError) as exc_info:
        FinishTool.create(unknown_param="value")
    assert "only accepts 'response_schema' parameter" in str(exc_info.value)


def test_create_structured_finish_action():
    """Test the helper function to create structured action types."""
    action_type = create_structured_finish_action(CodeReviewResult)

    # Should be a subclass of Action
    assert issubclass(action_type, Action)

    # Should have the schema fields
    fields = action_type.model_fields
    assert "score" in fields
    assert "issues_found" in fields
    assert "can_merge" in fields

    # Should NOT have 'message' field (from FinishAction)
    assert "message" not in fields


def test_structured_action_validation():
    """Test that structured actions can be instantiated and validated."""
    action_type = create_structured_finish_action(CodeReviewResult)

    # Create a valid action - using model_validate since fields are dynamic
    action = action_type.model_validate(
        {"score": 8, "issues_found": ["minor typo"], "can_merge": True}
    )
    action_data = action.model_dump(exclude={"kind"})

    assert action_data["score"] == 8
    assert action_data["issues_found"] == ["minor typo"]
    assert action_data["can_merge"] is True


def test_structured_action_schema():
    """Test that structured action produces correct MCP schema."""
    action_type = create_structured_finish_action(CodeReviewResult)
    schema = action_type.to_mcp_schema()

    assert "properties" in schema
    props = schema["properties"]

    assert "score" in props
    assert props["score"]["type"] == "integer"

    assert "issues_found" in props
    assert props["issues_found"]["type"] == "array"

    assert "can_merge" in props
    assert props["can_merge"]["type"] == "boolean"


def test_finish_executor_with_message_action():
    """Test FinishExecutor handles FinishAction with message field."""
    from openhands.sdk.tool.builtins.finish import FinishExecutor

    executor = FinishExecutor()
    action = FinishAction(message="Task completed!")
    obs = executor(action)

    assert isinstance(obs, FinishObservation)
    assert obs.text == "Task completed!"


def test_finish_executor_with_structured_action():
    """Test FinishExecutor handles structured actions without message field."""
    from openhands.sdk.tool.builtins.finish import FinishExecutor

    action_type = create_structured_finish_action(SimpleResult)
    action = action_type.model_validate(
        {"success": True, "summary": "All tests passed"}
    )

    executor = FinishExecutor()
    # Cast to FinishAction for type checking - executor handles both types
    obs = executor(action)  # type: ignore[arg-type]

    assert isinstance(obs, FinishObservation)
    # Should contain JSON representation
    assert "success" in obs.text
    assert "true" in obs.text.lower()
    assert "summary" in obs.text
    assert "All tests passed" in obs.text


def test_get_structured_response_with_structured_action():
    """Test extracting structured response from events."""
    action_type = create_structured_finish_action(CodeReviewResult)
    action = action_type.model_validate(
        {"score": 9, "issues_found": ["doc missing"], "can_merge": True}
    )

    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Review complete")],
        action=action,
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    events = [action_event]
    result = get_structured_response(events, CodeReviewResult)

    assert result is not None
    assert result.score == 9
    assert result.issues_found == ["doc missing"]
    assert result.can_merge is True


def test_get_structured_response_empty_events():
    """Test get_structured_response with empty events list."""
    result = get_structured_response([], CodeReviewResult)
    assert result is None


def test_get_structured_response_no_finish_action():
    """Test get_structured_response when no finish action exists."""
    # Create a non-finish action event
    tool_call = MessageToolCall(
        id="test-call-id", name="read_file", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Reading file")],
        action=None,
        tool_name="read_file",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    result = get_structured_response([action_event], CodeReviewResult)
    assert result is None


def test_get_structured_response_with_none_action():
    """Test get_structured_response when finish action is None."""
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Finishing")],
        action=None,  # No executable action
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    result = get_structured_response([action_event], CodeReviewResult)
    assert result is None


def test_get_structured_response_finds_last_finish():
    """Test get_structured_response returns the last finish action."""
    action_type = create_structured_finish_action(SimpleResult)

    # First finish action
    action1 = action_type.model_validate(
        {"success": False, "summary": "First attempt failed"}
    )
    tool_call1 = MessageToolCall(
        id="call-1", name="finish", arguments="{}", origin="completion"
    )
    event1 = ActionEvent(
        source="agent",
        thought=[TextContent(text="First")],
        action=action1,
        tool_name="finish",
        tool_call_id="call-1",
        tool_call=tool_call1,
        llm_response_id="resp-1",
    )

    # Second finish action (should be returned)
    action2 = action_type.model_validate(
        {"success": True, "summary": "Second attempt succeeded"}
    )
    tool_call2 = MessageToolCall(
        id="call-2", name="finish", arguments="{}", origin="completion"
    )
    event2 = ActionEvent(
        source="agent",
        thought=[TextContent(text="Second")],
        action=action2,
        tool_name="finish",
        tool_call_id="call-2",
        tool_call=tool_call2,
        llm_response_id="resp-2",
    )

    events = [event1, event2]
    result = get_structured_response(events, SimpleResult)

    assert result is not None
    assert result.success is True
    assert result.summary == "Second attempt succeeded"


def test_finish_tool_serialization():
    """Test that FinishTool with response_schema serializes correctly."""
    tools = FinishTool.create(response_schema=CodeReviewResult)
    tool = tools[0]

    # response_schema should be excluded from serialization
    dumped = tool.model_dump()
    assert "response_schema" not in dumped


def test_finish_tool_openai_tool_schema():
    """Test the OpenAI tool schema includes structured fields."""
    tools = FinishTool.create(response_schema=CodeReviewResult)
    tool = tools[0]

    openai_tool = tool.to_openai_tool()
    fn = openai_tool["function"]
    # Parameters is optional in the TypedDict but we know it's present
    assert "parameters" in fn
    params = fn["parameters"]  # type: ignore[typeddict-item]

    # Should include the schema fields
    props = params.get("properties", {})
    assert "score" in props
    assert "issues_found" in props
    assert "can_merge" in props


def test_finish_tool_backward_compatibility():
    """Test that original FinishAction still works with get_agent_final_response."""
    from openhands.sdk.conversation.response_utils import get_agent_final_response

    # Create a classic finish action event
    finish_action = FinishAction(message="Task completed successfully!")
    tool_call = MessageToolCall(
        id="test-call-id", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Finishing")],
        action=finish_action,
        tool_name="finish",
        tool_call_id="test-call-id",
        tool_call=tool_call,
        llm_response_id="test-response-id",
    )

    events = [action_event]
    result = get_agent_final_response(events)

    assert result == "Task completed successfully!"
