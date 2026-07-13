"""Tests for client-defined tools (ClientToolSpec / ClientTool)."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import TextContent
from openhands.sdk.llm.message import MessageToolCall
from openhands.sdk.tool.client_tool import (
    ClientTool,
    ClientToolExecutor,
    ClientToolObservation,
    ClientToolRegistrationError,
    ClientToolSpec,
    extract_client_tool_specs,
    register_client_tools,
)
from openhands.sdk.tool.registry import list_registered_tools, resolve_tool
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import ToolAnnotations, ToolDefinition


# ---------------------------------------------------------------------------
# ClientToolSpec
# ---------------------------------------------------------------------------


def test_spec_minimal():
    spec = ClientToolSpec(name="my_tool", description="Does stuff")
    assert spec.name == "my_tool"
    assert spec.description == "Does stuff"
    assert spec.parameters == {"type": "object", "properties": {}}
    assert spec.annotations is None


def test_spec_with_parameters():
    params: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to open",
            }
        },
        "required": ["file_path"],
    }
    spec = ClientToolSpec(
        name="open_file",
        description="Open a file",
        parameters=params,
    )
    assert spec.parameters["properties"]["file_path"]["type"] == "string"
    assert "file_path" in spec.parameters["required"]


def test_spec_with_annotations():
    ann = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
    spec = ClientToolSpec(
        name="delete_item",
        description="Delete something",
        annotations=ann,
    )
    assert spec.annotations is not None
    assert spec.annotations.destructiveHint is True
    assert spec.annotations.readOnlyHint is False


def test_spec_roundtrip_json():
    """Spec should survive JSON serialization/deserialization."""
    spec = ClientToolSpec(
        name="my_tool",
        description="Does stuff",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        },
    )
    data = spec.model_dump(mode="json")
    restored = ClientToolSpec.model_validate(data)
    assert restored == spec


# ---------------------------------------------------------------------------
# ClientToolExecutor
# ---------------------------------------------------------------------------


def test_executor_returns_acknowledgment():
    executor = ClientToolExecutor()
    # Create a minimal action to pass in
    action_type = Action.from_mcp_schema(
        "TestAction",
        {"type": "object", "properties": {}},
    )
    action = action_type()
    obs = executor(action)
    assert isinstance(obs, ClientToolObservation)
    assert obs.text == "Tool call dispatched to client."
    assert obs.is_error is False


# ---------------------------------------------------------------------------
# ClientTool
# ---------------------------------------------------------------------------


def test_from_spec_basic():
    spec = ClientToolSpec(name="ui_action", description="Do a UI thing")
    tool = ClientTool.from_spec(spec)

    assert isinstance(tool, ToolDefinition)
    assert tool.description == "Do a UI thing"
    assert tool.executor is not None
    assert tool.observation_type is ClientToolObservation


def test_from_spec_default_annotations_unset():
    """Without explicit annotations, client tools are left annotation-less.

    Client tools can trigger arbitrary frontend side effects, so we must not
    optimistically assume read-only/idempotent. Leaving annotations unset keeps
    the conservative behavior (e.g. security-risk prediction stays enabled).
    """
    spec = ClientToolSpec(name="view_panel", description="View panel")
    tool = ClientTool.from_spec(spec)
    assert tool.annotations is None


def test_from_spec_custom_annotations():
    ann = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
    spec = ClientToolSpec(
        name="mutate",
        description="Mutates state",
        annotations=ann,
    )
    tool = ClientTool.from_spec(spec)
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True


def test_from_spec_action_type_has_parameters():
    spec = ClientToolSpec(
        name="open_file",
        description="Open a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "line": {"type": "integer", "description": "Line number"},
            },
            "required": ["path"],
        },
    )
    tool = ClientTool.from_spec(spec)
    # The action type should have the parameters from the spec
    assert issubclass(tool.action_type, Action)
    schema = tool.action_type.to_mcp_schema()
    assert "path" in schema["properties"]
    assert "line" in schema["properties"]


def test_client_tool_callable():
    """Calling the tool through the normal path should return an ack."""
    spec = ClientToolSpec(
        name="navigate",
        description="Navigate to a page",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
        },
    )
    tool = ClientTool.from_spec(spec)
    action = tool.action_from_arguments({"url": "https://example.com"})
    obs = tool(action)
    assert isinstance(obs, ClientToolObservation)
    assert "dispatched" in obs.text.lower()


def test_client_tool_kind_argument_action_event_roundtrip():
    spec = ClientToolSpec(
        name="symbol_lookup",
        description="Lookup a symbol",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "description": "Symbol kind hint, such as Function or Method",
                },
            },
            "required": ["name"],
        },
    )
    tool = ClientTool.from_spec(spec)
    action = tool.action_from_arguments({"name": "executeCommand", "kind": "Method"})
    tool_call = MessageToolCall(
        id="call_123",
        name="symbol_lookup",
        arguments='{"name":"executeCommand","kind":"Method"}',
        origin="completion",
    )
    event = ActionEvent(
        thought=[TextContent(text="Look up a symbol")],
        action=action,
        tool_name="symbol_lookup",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )

    dumped = event.model_dump(mode="json")
    restored = ActionEvent.model_validate(dumped)

    assert restored.action is not None
    restored_action_dump = restored.action.model_dump()
    assert restored_action_dump["mcp_arg_kind"] == "Method"
    assert restored.action.kind == "ClientAction_symbol_lookup"


def test_client_tool_kind_argument_concrete_action_roundtrip():
    spec = ClientToolSpec(
        name="symbol_lookup",
        description="Lookup a symbol",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "description": "Symbol kind hint, such as Function or Method",
                },
            },
            "required": ["name"],
        },
    )
    tool = ClientTool.from_spec(spec)
    action_type = tool.action_type

    action = action_type.model_validate({"name": "executeCommand", "kind": "Method"})
    dumped = action.model_dump()
    restored = action_type.model_validate(dumped)

    assert dumped["mcp_arg_kind"] == "Method"
    assert dumped["kind"] == action_type.__name__
    assert restored.model_dump()["mcp_arg_kind"] == "Method"
    assert restored.kind == action_type.__name__


def test_create_classmethod():
    """The create() classmethod should return a single-element sequence."""
    spec = ClientToolSpec(name="test_tool", description="Test")
    tools = ClientTool.create(spec=spec)
    assert len(tools) == 1
    assert isinstance(tools[0], ClientTool)


def test_create_missing_spec_raises():
    """A missing ``spec`` raises a clear ValueError, not a leaked KeyError."""
    with pytest.raises(ValueError, match="requires a 'spec'"):
        ClientTool.create()


def test_create_accepts_dict_spec():
    """create() accepts a serialized (dict) spec, used by per-conversation params."""
    spec = ClientToolSpec(name="dict_tool", description="From dict")
    tools = ClientTool.create(spec=spec.model_dump())
    assert len(tools) == 1
    assert tools[0].name == "dict_tool"


def test_to_openai_tool():
    """Client tools should export valid OpenAI tool schema."""
    spec = ClientToolSpec(
        name="show_dialog",
        description="Show a dialog to the user",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    )
    tool = ClientTool.from_spec(spec)
    openai_tool = tool.to_openai_tool()
    assert openai_tool["type"] == "function"
    func = openai_tool["function"]
    assert func["name"] == "show_dialog"
    assert "description" in func
    assert func.get("description") == "Show a dialog to the user"
    params = func.get("parameters")
    assert isinstance(params, dict)
    assert "message" in params["properties"]


# ---------------------------------------------------------------------------
# Schema validation and constraint preservation
# ---------------------------------------------------------------------------


def test_spec_rejects_non_object_parameters():
    """parameters must be an object JSON Schema."""
    with pytest.raises(ValidationError, match="object JSON Schema"):
        ClientToolSpec(
            name="bad",
            description="bad params",
            parameters={"type": "string"},
        )


def test_openai_schema_preserves_client_constraints():
    """Client JSON Schema constraints (enum, nested) must reach the LLM schema."""
    spec = ClientToolSpec(
        name="set_level",
        description="Set a level",
        parameters={
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["info", "warning", "error"],
                },
                "config": {
                    "type": "object",
                    "properties": {"retries": {"type": "integer"}},
                },
            },
            "required": ["level"],
        },
    )
    tool = ClientTool.from_spec(spec)
    params = tool.to_openai_tool()["function"].get("parameters")
    assert isinstance(params, dict)
    # enum preserved
    assert params["properties"]["level"]["enum"] == ["info", "warning", "error"]
    # nested object properties preserved
    assert params["properties"]["config"]["properties"]["retries"]["type"] == "integer"
    # SDK meta field still injected
    assert "summary" in params["properties"]


def test_security_risk_injected_when_annotations_unset():
    """With no annotations, security-risk prediction is added (conservative)."""
    spec = ClientToolSpec(
        name="risky_default",
        description="Side effects",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    tool = ClientTool.from_spec(spec)
    params = tool.to_openai_tool(add_security_risk_prediction=True)["function"].get(
        "parameters"
    )
    assert isinstance(params, dict)
    assert "security_risk" in params["properties"]


def test_mcp_tool_uses_original_schema():
    spec = ClientToolSpec(
        name="mcp_export",
        description="Export",
        parameters={
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
        },
    )
    tool = ClientTool.from_spec(spec)
    mcp_tool = tool.to_mcp_tool()
    assert mcp_tool["inputSchema"]["properties"]["mode"]["enum"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Action-type caching / conflict handling
# ---------------------------------------------------------------------------


def test_same_spec_reuses_action_type():
    """Re-creating a tool with the same name+schema reuses the action type.

    This avoids creating duplicate concrete Action subclasses with the same
    kind, which would break Action.resolve_kind / event deserialization.
    """
    spec = ClientToolSpec(
        name="repeat_tool",
        description="Repeats",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    tool_a = ClientTool.from_spec(spec)
    tool_b = ClientTool.from_spec(spec)
    assert tool_a.action_type is tool_b.action_type


def test_same_name_different_schema_conflicts():
    """Same name with a different schema is rejected explicitly."""
    spec_a = ClientToolSpec(
        name="conflict_tool",
        description="A",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    spec_b = ClientToolSpec(
        name="conflict_tool",
        description="B",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
    )
    ClientTool.from_spec(spec_a)
    with pytest.raises(ValueError, match="different"):
        ClientTool.from_spec(spec_b)


# ---------------------------------------------------------------------------
# register_client_tools
# ---------------------------------------------------------------------------


def test_register_client_tools_returns_tool_specs():
    spec = ClientToolSpec(
        name="reg_tool",
        description="Registered",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )
    tool_specs = register_client_tools([spec])
    assert len(tool_specs) == 1
    ts = tool_specs[0]
    # Tool spec carries the serialized spec via params (per-conversation ownership)
    assert ts.name == "reg_tool"
    assert ts.params["spec"]["name"] == "reg_tool"
    assert ts.params["spec"]["parameters"]["properties"]["q"]["type"] == "string"
    assert extract_client_tool_specs([ts]) == [spec]
    # The ClientTool class is registered under the tool name
    assert "reg_tool" in list_registered_tools()


def test_register_client_tools_rejects_duplicate_names_in_single_request():
    name = "client_duplicate_in_request"
    spec = ClientToolSpec(name=name, description="One")

    with pytest.raises(
        ClientToolRegistrationError,
        match=f"Duplicate client tool name '{name}'",
    ):
        register_client_tools([spec, spec])

    assert name not in list_registered_tools()


def test_register_client_tools_allows_existing_client_tool_same_schema():
    spec = ClientToolSpec(
        name="client_reuse_same_schema",
        description="Reusable",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
    )

    first_tool_specs = register_client_tools([spec])
    second_tool_specs = register_client_tools([spec])

    assert [tool_spec.name for tool_spec in first_tool_specs] == [spec.name]
    assert [tool_spec.name for tool_spec in second_tool_specs] == [spec.name]
    resolved_tools = resolve_tool(
        second_tool_specs[0], MagicMock(spec=ConversationState)
    )
    assert len(resolved_tools) == 1
    assert isinstance(resolved_tools[0], ClientTool)
    assert resolved_tools[0].name == spec.name
