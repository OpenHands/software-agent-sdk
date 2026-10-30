from typing import Any, cast
from unittest.mock import Mock

import mcp.types
import pytest
from pydantic import ValidationError

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition


def _make_tool_with_schema(schema: dict, name: str = "fetch"):
    mcp_tool = mcp.types.Tool(
        name=name,
        description="Fetch a URL",
        inputSchema=schema,
    )
    client = Mock(spec=MCPClient)
    return MCPToolDefinition.create(mcp_tool, client)[0]


def test_mcp_action_from_arguments_validates_and_sanitizes():
    tool = _make_tool_with_schema(
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["url"],
        }
    )

    # includes a None that should be dropped
    args = {"url": "https://example.com", "timeout": None}
    action = tool.action_from_arguments(args)
    # Note: 'kind' field from DiscriminatedUnionMixin should NOT be in action.data
    # because it's not part of the MCP tool schema and would cause validation errors
    # when sent to the MCP server
    assert action.data == {"url": "https://example.com"}


def test_mcp_action_from_arguments_preserves_schema_kind_argument():
    tool = _make_tool_with_schema(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file_path": {"type": "string"},
                "kind": {
                    "type": "string",
                    "description": "Symbol kind hint, such as Function or Method",
                },
                "repo": {"type": "string"},
            },
            "required": ["name"],
        },
        name="gitnexus_context",
    )

    function = tool.to_openai_tool()["function"]
    openai_schema = cast(dict[str, Any], function.get("parameters"))
    properties = cast(dict[str, Any], openai_schema["properties"])
    kind_schema = cast(dict[str, Any], properties["kind"])
    assert kind_schema["description"] == "Symbol kind hint, such as Function or Method"

    action = tool.action_from_arguments(
        {
            "name": "executeCommand",
            "file_path": "src/vs/workbench/services/commands/common/commandService.ts",
            "kind": "Method",
            "repo": "vscode-benchmark-repo",
        }
    )

    assert action.data == {
        "name": "executeCommand",
        "file_path": "src/vs/workbench/services/commands/common/commandService.ts",
        "kind": "Method",
        "repo": "vscode-benchmark-repo",
    }


def test_mcp_action_from_arguments_raises_on_invalid():
    tool = _make_tool_with_schema(
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
            "required": ["url"],
        }
    )

    # missing required url
    with pytest.raises(ValidationError):
        tool.action_from_arguments({})

    # extra field should also cause validation error
    with pytest.raises(ValidationError):
        tool.action_from_arguments({"url": "https://x.com", "data": {"x": 1}})


def test_mcp_action_from_arguments_wrong_type_raises_validation_error():
    tool = _make_tool_with_schema(
        {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": ["limit"],
        }
    )

    with pytest.raises(ValidationError):
        tool.action_from_arguments({"limit": "not-a-number"})


def test_mcp_action_from_arguments_without_properties_accepts_empty_call():
    tool = _make_tool_with_schema({"type": "object"})

    assert tool.action_from_arguments({}).data == {}
    with pytest.raises(ValidationError):
        tool.action_from_arguments({"unexpected": 1})


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: Schema.from_mcp_schema passes MCP argument names straight to "
        "pydantic.create_model, so '_'-prefixed names raise NameError, which "
        "Agent._get_action_event does not catch"
    ),
)
def test_mcp_action_from_arguments_accepts_underscore_prefixed_argument():
    tool = _make_tool_with_schema(
        {
            "type": "object",
            "properties": {"_cursor": {"type": "string"}},
            "required": ["_cursor"],
        },
        name="paginate",
    )

    assert tool.action_from_arguments({"_cursor": "abc"}).data == {"_cursor": "abc"}
