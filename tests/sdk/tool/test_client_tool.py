"""Tests for client-defined tools (ClientToolSpec / ClientTool)."""

from typing import Any

import pytest

from openhands.sdk.tool.client_tool import (
    ClientTool,
    ClientToolExecutor,
    ClientToolObservation,
    ClientToolSpec,
)
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


def test_from_spec_default_annotations():
    """Without explicit annotations, client tools default to read-only."""
    spec = ClientToolSpec(name="view_panel", description="View panel")
    tool = ClientTool.from_spec(spec)
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False


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


def test_create_classmethod():
    """The create() classmethod should return a single-element sequence."""
    spec = ClientToolSpec(name="test_tool", description="Test")
    tools = ClientTool.create(spec=spec)
    assert len(tools) == 1
    assert isinstance(tools[0], ClientTool)


def test_create_missing_spec_raises():
    with pytest.raises(KeyError):
        ClientTool.create()


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
