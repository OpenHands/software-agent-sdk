"""Property tests for the parameters and arguments of MCP tools.

For any object schema an MCP server advertises, the Chat Completions and
Responses renderings of the tool expose the same parameter paths, every
declared parameter is present, and the only additions are the OpenHands
``summary`` and ``security_risk`` fields. Arguments that match the schema
reach the MCP server exactly as the LLM sent them.
"""

import itertools
from typing import Any
from unittest.mock import Mock

import mcp.types
from hypothesis import given, strategies as st

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition


INJECTED_PATHS = {("summary",), ("security_risk",)}

parameter_names = (
    st.sampled_from(["url", "query", "name", "id", "kind", "type", "path", "data"])
    | st.from_regex(r"[a-z][a-z0-9_]{0,6}", fullmatch=True)
).filter(lambda name: name not in ("summary", "security_risk"))

primitive_schemas = st.sampled_from(["string", "integer", "number", "boolean"]).map(
    lambda json_type: {"type": json_type}
)


@st.composite
def object_schemas(
    draw: st.DrawFn, children: st.SearchStrategy[dict[str, Any]]
) -> dict[str, Any]:
    properties = draw(st.dictionaries(parameter_names, children, max_size=4))
    required = [name for name in properties if draw(st.booleans())]
    return {"type": "object", "properties": properties, "required": required}


input_schemas = object_schemas(
    st.recursive(
        primitive_schemas,
        lambda children: (
            object_schemas(children)
            | children.map(lambda items: {"type": "array", "items": items})
        ),
        max_leaves=8,
    )
)

json_scalars = st.none() | st.booleans() | st.integers() | st.text(max_size=4)
argument_values: dict[str, st.SearchStrategy[Any]] = {
    "string": st.text(max_size=8),
    "integer": st.integers(min_value=-(10**6), max_value=10**6),
    "number": st.floats(allow_nan=False, allow_infinity=False),
    "boolean": st.booleans(),
    "array": st.lists(json_scalars, max_size=3),
    "object": st.dictionaries(st.text(max_size=4), json_scalars, max_size=3),
}


@st.composite
def schemas_with_arguments(
    draw: st.DrawFn,
) -> tuple[dict[str, Any], dict[str, Any]]:
    json_types = draw(
        st.dictionaries(
            parameter_names, st.sampled_from(sorted(argument_values)), max_size=5
        )
    )
    arguments = {
        name: draw(argument_values[json_type])
        for name, json_type in json_types.items()
        if draw(st.booleans())
    }
    input_schema = {
        "type": "object",
        "properties": {
            name: {"type": json_type} for name, json_type in json_types.items()
        },
        "required": [name for name in arguments if draw(st.booleans())],
    }
    return input_schema, arguments


_tool_ids = itertools.count()


def _mcp_tool(input_schema: dict[str, Any]) -> MCPToolDefinition:
    mcp_tool = mcp.types.Tool(
        name=f"tool_{next(_tool_ids)}", description="test", inputSchema=input_schema
    )
    return MCPToolDefinition.create(mcp_tool, Mock(spec=MCPClient))[0]


def _rendered_parameters(
    tool: MCPToolDefinition, add_security_risk_prediction: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    chat = tool.to_openai_tool(
        add_security_risk_prediction=add_security_risk_prediction
    )["function"].get("parameters")
    responses = tool.to_responses_tool(
        add_security_risk_prediction=add_security_risk_prediction
    )["parameters"]
    assert chat is not None
    assert responses is not None
    return chat, responses


def _parameter_paths(
    schema: dict[str, Any], prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for name, child in schema.get("properties", {}).items():
        paths.add((*prefix, name))
        paths |= _parameter_paths(child, (*prefix, name))
    items = schema.get("items")
    if isinstance(items, dict):
        paths |= _parameter_paths(items, (*prefix, "[]"))
    return paths


@given(input_schema=input_schemas, add_security_risk_prediction=st.booleans())
def test_chat_and_responses_expose_the_same_parameters(
    input_schema: dict[str, Any], add_security_risk_prediction: bool
):
    chat, responses = _rendered_parameters(
        _mcp_tool(input_schema), add_security_risk_prediction
    )

    assert _parameter_paths(chat) == _parameter_paths(responses)


@given(input_schema=input_schemas, add_security_risk_prediction=st.booleans())
def test_rendered_parameters_are_the_declared_ones(
    input_schema: dict[str, Any], add_security_risk_prediction: bool
):
    declared = _parameter_paths(input_schema)

    for rendered in _rendered_parameters(
        _mcp_tool(input_schema), add_security_risk_prediction
    ):
        rendered_paths = _parameter_paths(rendered)
        assert declared <= rendered_paths
        assert rendered_paths - declared <= INJECTED_PATHS


@given(schema_and_arguments=schemas_with_arguments())
def test_call_arguments_reach_the_server_unchanged(
    schema_and_arguments: tuple[dict[str, Any], dict[str, Any]],
):
    input_schema, arguments = schema_and_arguments

    action = _mcp_tool(input_schema).action_from_arguments(dict(arguments))

    assert action.data == arguments
    assert action.to_mcp_arguments() == arguments
