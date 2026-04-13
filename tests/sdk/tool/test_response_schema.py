"""Tests for the ``response_schema`` structured-output mechanism on tools."""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field, ValidationError

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.tool.builtins.finish import (
    FinishAction,
    FinishObservation,
    FinishTool,
)
from openhands.sdk.tool.registry import register_tool, resolve_tool
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import ToolDefinition, _create_action_type_with_schema


class TaskResult(BaseModel):
    success: bool = Field(description="Whether the task succeeded.")
    summary_text: str = Field(description="One-line summary of what was done.")
    files_changed: list[str] = Field(default_factory=list)


def _finish_with_schema(schema: type[BaseModel]) -> ToolDefinition:
    """Resolve a FinishTool instance via the registry with response_schema set."""
    register_tool("FinishTool", FinishTool)
    [tool] = resolve_tool(
        Tool(name="FinishTool", params={"response_schema": schema}),
        conv_state=MagicMock(),
    )
    return tool


def _make_finish_event(tool: ToolDefinition, tool_name: str, **fields) -> ActionEvent:
    defaults = {
        "message": "m",
        "success": True,
        "summary_text": "s",
        "files_changed": [],
    }
    defaults.update(fields)
    action = tool.action_from_arguments(defaults)
    return ActionEvent(
        tool_name=tool_name,
        tool_call_id="tc",
        tool_call=MessageToolCall(
            id="tc", name=tool_name, arguments="{}", origin="completion"
        ),
        llm_response_id="r",
        action=action,
        thought=[],
        reasoning_content="",
    )


def test_finish_tool_without_schema_is_unchanged():
    [tool] = FinishTool.create()
    assert tool.response_schema is None
    schema = tool._get_tool_schema()
    assert set(schema["properties"]) == {"message", "summary"}


def test_response_schema_extends_action_schema():
    tool = _finish_with_schema(TaskResult)
    assert tool.response_schema is TaskResult
    props = tool._get_tool_schema()["properties"]
    assert {"message", "success", "summary_text", "files_changed"} <= set(props)
    # original Pydantic descriptions are preserved for the LLM
    assert props["success"]["description"] == "Whether the task succeeded."


def test_action_from_arguments_validates_extended_payload():
    tool = _finish_with_schema(TaskResult)
    action = tool.action_from_arguments(
        {
            "message": "done",
            "success": True,
            "summary_text": "fixed bug",
            "files_changed": ["a.py", "b.py"],
        }
    )
    assert isinstance(action, FinishAction)
    assert action.message == "done"
    typed = tool.parse_response(action)
    assert isinstance(typed, TaskResult)
    assert typed.success is True
    assert typed.files_changed == ["a.py", "b.py"]


@pytest.mark.parametrize(
    "bad_payload",
    [
        pytest.param({"message": "done"}, id="missing-all-schema-fields"),
        pytest.param(
            {"message": "done", "success": True, "files_changed": []},
            id="missing-summary_text",
        ),
        pytest.param(
            {
                "message": "done",
                "success": {"not": "a bool"},
                "summary_text": "s",
                "files_changed": [],
            },
            id="wrong-type-for-bool",
        ),
        pytest.param(
            {
                "message": "done",
                "success": True,
                "summary_text": "s",
                "files_changed": "not-a-list",
            },
            id="wrong-type-for-list",
        ),
    ],
)
def test_action_from_arguments_rejects_invalid_payload(bad_payload):
    tool = _finish_with_schema(TaskResult)
    with pytest.raises(ValidationError):
        tool.action_from_arguments(bad_payload)


def test_nested_pydantic_schema_roundtrips():
    """Nested Pydantic models: descriptions flow to the LLM schema, and
    ``parse_response`` reconstructs the full tree typed."""

    class Change(BaseModel):
        path: str = Field(description="File that changed.")
        lines: int = Field(description="Lines changed.")

    class NestedResult(BaseModel):
        headline: str
        changes: list[Change]

    tool = _finish_with_schema(NestedResult)
    props = tool._get_tool_schema()["properties"]
    change_props = props["changes"]["items"]["properties"]
    assert change_props["path"]["description"] == "File that changed."

    action = tool.action_from_arguments(
        {
            "message": "ok",
            "headline": "big refactor",
            "changes": [
                {"path": "a.py", "lines": 3},
                {"path": "b.py", "lines": 7},
            ],
        }
    )
    typed = tool.parse_response(action)
    assert isinstance(typed, NestedResult)
    assert typed.changes[1].path == "b.py"
    assert isinstance(typed.changes[0], Change)


def test_parse_response_requires_schema():
    [tool] = FinishTool.create()
    with pytest.raises(ValueError):
        tool.parse_response(FinishAction(message="hi"))


def test_executor_still_works_with_schema():
    tool = _finish_with_schema(TaskResult)
    action = tool.action_from_arguments(
        {"message": "ok", "success": True, "summary_text": "ok", "files_changed": []}
    )
    obs = tool(action)
    assert isinstance(obs, FinishObservation)


@pytest.mark.parametrize(
    "params, expected_serialised",
    [
        pytest.param({"response_schema": TaskResult}, {}, id="schema-only-dropped"),
        pytest.param(
            {"response_schema": TaskResult, "keep_me": 7, "opts": {"a": 1}},
            {"keep_me": 7, "opts": {"a": 1}},
            id="schema-dropped-others-kept",
        ),
        pytest.param({"keep_me": 7}, {"keep_me": 7}, id="no-schema"),
    ],
)
def test_tool_spec_strips_class_valued_params_on_dump(params, expected_serialised):
    """Regression: a class-valued param must not break ``model_dump_json`` —
    otherwise persisting conversation state crashes at ``_save_base_state``.
    The class is runtime-only and is dropped on dump; the registry re-applies
    it from the in-memory spec on resolve.
    """
    spec = Tool(name="FinishTool", params=params)
    assert json.loads(spec.model_dump_json())["params"] == expected_serialised


def test_create_action_type_with_schema_is_cached():
    """Schema augmentation is called on every LLM serialization; it must be
    cached, not rebuild a class every time."""
    a = _create_action_type_with_schema(FinishAction, TaskResult)
    b = _create_action_type_with_schema(FinishAction, TaskResult)
    assert a is b


def test_parse_last_response_ignores_other_tools():
    """Must match on ``tool_name`` — an event from a different tool doesn't
    count."""
    tool = _finish_with_schema(TaskResult)
    events = [
        _make_finish_event(tool, tool_name="finish"),
        _make_finish_event(tool, tool_name="something_else"),
    ]
    result = tool.parse_last_response(events)
    assert isinstance(result, TaskResult)


def test_parse_last_response_picks_most_recent():
    tool = _finish_with_schema(TaskResult)
    events = [
        _make_finish_event(tool, tool_name="finish", success=False),
        _make_finish_event(tool, tool_name="finish", success=True),
    ]
    result = tool.parse_last_response(events)
    assert isinstance(result, TaskResult)
    assert result.success is True
    assert tool.parse_last_response([]) is None


def test_field_collision_raises():
    class Bad(BaseModel):
        message: str  # collides with FinishAction.message

    tool = _finish_with_schema(Bad)
    with pytest.raises(ValueError, match="collide"):
        tool._get_tool_schema()
