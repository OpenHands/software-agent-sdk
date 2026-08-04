"""Tests for the ``response_schema`` structured-output mechanism on tools."""

import json
from unittest.mock import MagicMock, Mock

import mcp.types
import pytest
from pydantic import BaseModel, Field, ValidationError

from openhands.sdk.agent.utils import fix_malformed_tool_arguments
from openhands.sdk.event import ActionEvent, Event
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool.builtins.finish import (
    FinishAction,
    FinishObservation,
    FinishTool,
)
from openhands.sdk.tool.client_tool import ClientTool, ClientToolSpec
from openhands.sdk.tool.registry import register_tool, resolve_tool
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import ToolDefinition


class TaskResult(BaseModel):
    success: bool = Field(description="Whether the task succeeded.")
    summary_text: str = Field(description="One-line summary of what was done.")
    files_changed: list[str] = Field(default_factory=list)


class FinishPairTool(FinishTool):
    @classmethod
    def create(cls, conv_state=None, **params):
        [tool] = super().create(conv_state, **params)
        return [tool, tool]


def _finish_with_schema(
    schema: type[BaseModel] | dict[str, object],
) -> ToolDefinition:
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
            id="tc", name=tool_name, arguments=json.dumps(defaults), origin="completion"
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
    assert type(action) is FinishAction
    assert action.kind == "FinishAction"
    assert action.message == "done"
    assert action.structured_output == {
        "success": True,
        "summary_text": "fixed bug",
        "files_changed": ["a.py", "b.py"],
    }
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


def test_parse_response_requires_structured_output():
    tool = _finish_with_schema(TaskResult)
    with pytest.raises(ValueError, match="no structured output"):
        tool.parse_response(FinishAction(message="hi"))


def test_executor_still_works_with_schema():
    tool = _finish_with_schema(TaskResult)
    action = tool.action_from_arguments(
        {"message": "ok", "success": True, "summary_text": "ok", "files_changed": []}
    )
    obs = tool(action)
    assert isinstance(obs, FinishObservation)


def test_tool_spec_roundtrips_response_schema():
    spec = Tool(
        name="FinishTool",
        params={"response_schema": TaskResult},
    )
    assert spec.model_dump()["params"]["response_schema"] is TaskResult

    dumped = json.loads(spec.model_dump_json())
    assert "success" in dumped["params"]["response_schema"]["properties"]

    register_tool("FinishTool", FinishTool)
    restored = Tool.model_validate(dumped)
    [tool] = resolve_tool(restored, conv_state=MagicMock())
    assert isinstance(tool.response_schema, dict)
    assert "success" in tool._get_tool_schema()["properties"]
    action = tool.action_from_arguments(
        {
            "message": "done",
            "success": True,
            "summary_text": "fixed",
            "files_changed": [],
        }
    )
    result = tool.parse_response(action)
    assert isinstance(result, dict)
    assert result["success"] is True


def test_action_event_roundtrips_with_static_kind():
    tool = _finish_with_schema(TaskResult)
    event = _make_finish_event(tool, tool_name="finish")

    serialized = event.model_dump_json()
    assert "FinishActionWith" not in serialized
    assert "structured_output" not in serialized
    restored = Event.model_validate_json(serialized)

    assert isinstance(restored, ActionEvent)
    assert type(restored.action) is FinishAction
    assert restored.action.structured_output is None
    result = tool.parse_last_response([restored])
    assert isinstance(result, TaskResult)
    assert result.success is True
    assert restored.action.structured_output is None


def test_response_schema_preserves_constraints():
    class ConstrainedResult(BaseModel):
        code: str = Field(pattern=r"^[A-Z]{3}$", min_length=3, max_length=3)
        count: int = Field(ge=1, le=5)

    tool = _finish_with_schema(ConstrainedResult)
    properties = tool._get_tool_schema()["properties"]

    assert properties["code"]["pattern"] == r"^[A-Z]{3}$"
    assert properties["code"]["minLength"] == 3
    assert properties["code"]["maxLength"] == 3
    assert properties["count"]["minimum"] == 1
    assert properties["count"]["maximum"] == 5


def test_response_schema_preserves_additional_properties_false():
    tool = _finish_with_schema(
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        }
    )

    assert tool._get_tool_schema()["additionalProperties"] is False


def test_response_schema_rejects_object_level_constraints():
    schema = {
        "type": "object",
        "properties": {
            "foo": {"type": "string"},
            "bar": {"type": "string"},
        },
        "dependentRequired": {"foo": ["bar"]},
    }

    with pytest.raises(ValueError, match="unsupported.*dependentRequired"):
        _finish_with_schema(schema)


@pytest.mark.parametrize(
    "additional_properties",
    [True, {"type": "string"}],
    ids=["untyped", "typed"],
)
def test_response_schema_rejects_dynamic_fields(additional_properties):
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "additionalProperties": additional_properties,
    }

    with pytest.raises(ValueError, match="dynamic fields"):
        _finish_with_schema(schema)


def test_response_schema_requires_required_fields_to_be_named():
    schema = {
        "type": "object",
        "properties": {"foo": {"type": "string"}},
        "required": ["bar"],
    }

    with pytest.raises(ValueError, match="required fields.*bar.*named properties"):
        _finish_with_schema(schema)


def test_response_schema_requires_named_properties():
    with pytest.raises(ValueError, match="named properties"):
        _finish_with_schema(
            {"type": "object", "additionalProperties": {"type": "string"}}
        )


def test_parse_last_response_ignores_other_tools():
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
        message: str

    with pytest.raises(ValueError, match="collide"):
        _finish_with_schema(Bad)


@pytest.mark.parametrize(
    "reserved_name", ["kind", "security_risk", "structured_output", "summary"]
)
def test_reserved_meta_field_names_raise(reserved_name):
    ReservedSchema = type(
        "ReservedSchema",
        (BaseModel,),
        {"__annotations__": {reserved_name: str}},
    )

    with pytest.raises(ValueError, match="reserved"):
        _finish_with_schema(ReservedSchema)


@pytest.mark.parametrize(
    "schema", [TaskResult, TaskResult.model_json_schema()], ids=["model", "json"]
)
def test_response_fields_use_malformed_argument_repairs(schema):
    arguments = fix_malformed_tool_arguments(
        {"files_changed": '["a.py", "b.py"]'}, schema
    )
    assert arguments["files_changed"] == ["a.py", "b.py"]


def test_response_schema_rejects_toolsets():
    register_tool("FinishPairTool", FinishPairTool)
    with pytest.raises(ValueError, match="exactly one tool"):
        resolve_tool(
            Tool(
                name="FinishPairTool",
                params={"response_schema": TaskResult},
            ),
            conv_state=MagicMock(),
        )


def test_client_tool_supports_response_schema():
    tool = ClientTool.from_spec(
        ClientToolSpec(
            name="response_schema_client_test",
            description="Ask a question",
            parameters={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        )
    ).set_response_schema(TaskResult)

    properties = tool._get_tool_schema()["properties"]
    assert {"question", "success", "summary_text"} <= set(properties)
    assert "success" in tool.to_mcp_tool()["inputSchema"]["properties"]
    action = tool.action_from_arguments(
        {
            "question": "Proceed?",
            "success": True,
            "summary_text": "asked",
            "files_changed": [],
        }
    )
    assert action.structured_output is not None
    assert action.structured_output["success"] is True


def test_mcp_tool_supports_response_schema():
    mcp_tool = mcp.types.Tool(
        name="response_schema_mcp_test",
        description="Fetch a URL",
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )
    [tool] = MCPToolDefinition.create(mcp_tool, Mock(spec=MCPClient))
    tool = tool.set_response_schema(TaskResult)

    assert "success" in tool._get_tool_schema()["properties"]
    assert "success" in tool.to_mcp_tool()["inputSchema"]["properties"]
    action = tool.action_from_arguments(
        {
            "url": "https://example.com",
            "success": True,
            "summary_text": "fetched",
            "files_changed": [],
        }
    )
    assert action.data == {"url": "https://example.com"}
    assert action.structured_output is not None
    assert action.structured_output["success"] is True


def test_response_schema_json_is_cached_per_class():
    """The Pydantic model_json_schema() result is cached by the immutable class so
    repeated _response_schema_json calls reuse the cached schema."""
    from openhands.sdk.tool.tool import _response_schema_json_cache

    # Prime the cache by resolving a tool with a schema.
    _finish_with_schema(TaskResult)
    assert TaskResult in _response_schema_json_cache

    cached = _response_schema_json_cache[TaskResult]
    # Subsequent calls return deep copies of the same cached schema.
    again = _finish_with_schema(TaskResult)
    assert _response_schema_json_cache[TaskResult] == cached
    # Multiple action_from_arguments calls do not regenerate the cache.
    again.action_from_arguments(
        {"message": "m", "success": True, "summary_text": "s", "files_changed": []}
    )
    again.action_from_arguments(
        {"message": "m2", "success": False, "summary_text": "s2", "files_changed": []}
    )
    assert _response_schema_json_cache[TaskResult] == cached


def test_response_schema_cache_does_not_go_stale_on_model_copy():
    """A tool rebuilt via model_copy (bypassing set_response_schema) with a
    different schema must not reuse a stale cache."""
    tool = _finish_with_schema(TaskResult)

    class OtherResult(BaseModel):
        score: int = Field(description="A score.")
        summary_text: str = Field(description="One-line summary.")

    bypassed = tool.model_copy(update={"response_schema": OtherResult})
    action = bypassed.action_from_arguments(
        {"message": "m", "score": 5, "summary_text": "s"}
    )
    # score routed to the structured output, not swallowed as a stale field
    assert action.structured_output == {"score": 5, "summary_text": "s"}
    assert isinstance(action, FinishAction)
    assert action.message == "m"
