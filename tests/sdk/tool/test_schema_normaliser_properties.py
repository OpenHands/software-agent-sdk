"""Property tests for normalising the JSON Schema that MCP servers advertise.

Any JSON Schema (boolean schemas, ``type`` unions, single and tuple-form
``items``, ``anyOf``, and ``$ref`` into ``$defs`` that may be cyclic or
dangling) can be normalised into JSON and turned into an action model without
raising. In ``anyOf``, a ``false`` branch or a ``{"type": "null"}`` branch
does not change how the other branch is normalised.
"""

import json
from typing import Any

from hypothesis import assume, given, strategies as st

from openhands.sdk.tool.schema import Schema, _process_schema_node


SchemaNode = bool | dict[str, Any]

json_types = st.sampled_from(
    ["string", "integer", "number", "boolean", "object", "array", "null"]
)
definition_names = st.sampled_from(["A", "B"])
property_names = st.sampled_from(["a", "b", "c"])

leaf_schemas: st.SearchStrategy[SchemaNode] = st.one_of(
    st.booleans(),
    json_types.map(lambda json_type: {"type": json_type}),
    st.lists(json_types, min_size=2, max_size=3, unique=True).map(
        lambda union: {"type": union}
    ),
    definition_names.map(lambda name: {"$ref": f"#/$defs/{name}"}),
)


def _compound_schemas(
    children: st.SearchStrategy[SchemaNode],
) -> st.SearchStrategy[SchemaNode]:
    return st.one_of(
        st.dictionaries(property_names, children, max_size=3).map(
            lambda properties: {"type": "object", "properties": properties}
        ),
        children.map(lambda items: {"type": "array", "items": items}),
        st.lists(children, min_size=1, max_size=3).map(
            lambda items: {"type": "array", "items": items}
        ),
        st.lists(children, min_size=1, max_size=3).map(
            lambda branches: {"anyOf": branches}
        ),
    )


schema_nodes = st.recursive(leaf_schemas, _compound_schemas, max_leaves=10)
definitions = st.dictionaries(definition_names, schema_nodes, max_size=2)


@given(
    properties=st.dictionaries(property_names, schema_nodes, max_size=3),
    defs=definitions,
)
def test_any_input_schema_normalises_without_raising(
    properties: dict[str, SchemaNode], defs: dict[str, SchemaNode]
):
    input_schema = {"type": "object", "properties": properties, "$defs": defs}

    json.dumps(_process_schema_node(input_schema, defs))
    Schema.from_mcp_schema("GeneratedAction", input_schema)


@given(schema=schema_nodes, defs=definitions)
def test_false_and_null_branches_are_anyof_identities(
    schema: SchemaNode, defs: dict[str, SchemaNode]
):
    assume(not (isinstance(schema, dict) and schema.get("type") == "null"))
    expected = _process_schema_node(schema, defs)

    for branches in ([schema, False], [False, schema], [schema, {"type": "null"}]):
        assert _process_schema_node({"anyOf": branches}, defs) == expected
