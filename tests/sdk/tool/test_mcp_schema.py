"""Tests for MCP schema generation in openhands.sdk.tool.schema."""

import json
from collections.abc import Sequence

import pytest
from pydantic import Field

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool.schema import Action, Observation, Schema, _process_schema_node


class MCPSchemaTestAction(Action):
    """Test action class for MCP schema testing."""

    command: str = Field(description="Command to execute")
    optional_field: str | None = Field(default=None, description="Optional field")


class MCPComplexAction(Action):
    """Action with complex types."""

    simple_field: str = Field(description="Simple string field")
    optional_int: int | None = Field(default=None, description="Optional integer")
    string_list: list[str] = Field(default_factory=list, description="List of strings")


class MCPSchemaTestObservation(Observation):
    """Test observation class for MCP schema testing."""

    result: str = Field(description="Result of the action")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


def test_action_to_mcp_schema_excludes_kind():
    """Test that Action.to_mcp_schema() excludes the 'kind' field."""
    schema = MCPSchemaTestAction.to_mcp_schema()

    # The 'kind' field should not be in properties
    assert "kind" not in schema["properties"], (
        "'kind' field should not be present in MCP schema properties"
    )

    # The 'kind' field should not be in required
    if "required" in schema:
        assert "kind" not in schema["required"], (
            "'kind' field should not be present in MCP schema required list"
        )


def test_action_to_mcp_schema_includes_actual_fields():
    """Test that to_mcp_schema() includes the actual action fields."""
    schema = MCPSchemaTestAction.to_mcp_schema()

    # Should include the actual fields
    assert "command" in schema["properties"]
    assert "optional_field" in schema["properties"]

    # Check field descriptions
    assert schema["properties"]["command"]["description"] == "Command to execute"
    assert schema["properties"]["optional_field"]["description"] == "Optional field"

    # Required fields should be marked correctly
    assert "command" in schema["required"]


def test_observation_to_mcp_schema_excludes_kind():
    """Test that Observation.to_mcp_schema() excludes the 'kind' field."""
    schema = MCPSchemaTestObservation.to_mcp_schema()

    # The 'kind' field should not be in properties
    assert "kind" not in schema["properties"], (
        "'kind' field should not be present in MCP schema properties"
    )

    # The 'kind' field should not be in required
    if "required" in schema:
        assert "kind" not in schema["required"], (
            "'kind' field should not be present in MCP schema required list"
        )


def test_complex_action_to_mcp_schema_excludes_kind():
    """Test that complex Action types also exclude 'kind' field."""
    schema = MCPComplexAction.to_mcp_schema()

    # The 'kind' field should not be in properties
    assert "kind" not in schema["properties"], (
        "'kind' field should not be present in MCP schema properties"
    )

    # Should include all the actual fields
    assert "simple_field" in schema["properties"]
    assert "optional_int" in schema["properties"]
    assert "string_list" in schema["properties"]

    # Check types are correct
    assert schema["properties"]["simple_field"]["type"] == "string"
    assert schema["properties"]["optional_int"]["type"] == "integer"
    assert schema["properties"]["string_list"]["type"] == "array"


def test_mcp_schema_structure():
    """Test that MCP schema has the correct structure."""
    schema = MCPSchemaTestAction.to_mcp_schema()

    # Should have type and properties
    assert schema["type"] == "object"
    assert "properties" in schema
    assert isinstance(schema["properties"], dict)

    # Should have description if provided
    assert "description" in schema
    assert schema["description"] == "Test action class for MCP schema testing."

    # Should have required list
    assert "required" in schema
    assert isinstance(schema["required"], list)


def test_kind_field_works_for_discriminated_union():
    """Test that 'kind' field still works for internal discriminated unions."""
    # Create an instance - this should work fine
    action = MCPSchemaTestAction(command="test")

    # The instance should have the 'kind' field set correctly
    assert hasattr(action, "kind")
    assert action.kind == "MCPSchemaTestAction"

    # Serialization should include 'kind'
    dumped = action.model_dump()
    assert "kind" in dumped
    assert dumped["kind"] == "MCPSchemaTestAction"

    # Deserialization should work with 'kind'
    data = {"kind": "MCPSchemaTestAction", "command": "test"}
    restored = MCPSchemaTestAction.model_validate(data)
    assert restored.command == "test"
    assert restored.kind == "MCPSchemaTestAction"


class TestCircularSchemaHandling:
    """Tests for handling circular $ref schemas in tool schemas.

    These tests verify that _process_schema_node correctly handles schemas
    with circular $ref patterns without causing RecursionError.

    Related: Datadog logs from conversation ab9909a07571431a86ab6f1be36f555f
    """

    def test_circular_ref_in_raw_schema_handled_gracefully(self):
        """Test that a raw schema with circular $ref is handled gracefully.

        The fix detects circular $ref patterns and breaks the cycle by
        returning a generic object type instead of recursing infinitely.
        """
        # Create a schema with circular $ref - this is what an MCP tool
        # with recursive data structures would look like
        circular_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "children": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/TreeNode"},
                },
            },
            "$defs": {
                "TreeNode": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/TreeNode"},  # Circular!
                        },
                    },
                }
            },
        }

        defs = circular_schema.get("$defs", {})

        # This should NOT raise RecursionError - the fix handles circular refs
        result = _process_schema_node(circular_schema, defs)

        # Verify the result is valid and JSON-serializable
        assert result["type"] == "object"
        assert "properties" in result
        json.dumps(result)  # Should not raise

    def test_self_referential_pydantic_schema_handled_gracefully(self):
        """Test that a self-referential Pydantic Schema is handled gracefully.

        This is the real-world scenario: a Pydantic model with self-referential
        fields (like a tree node) generates a JSON schema with circular $ref.
        The fix ensures to_mcp_schema() works without RecursionError.

        This test verifies the fix for the bug that caused:
            litellm.APIConnectionError: OpenrouterException -
            maximum recursion depth exceeded
        in conversation ab9909a07571431a86ab6f1be36f555f
        """

        # Create a self-referential Schema (tree structure)
        class TreeNode(Schema):
            """A tree node that can have children of the same type."""

            value: str = Field(description="The value of this node")
            children: list["TreeNode"] | None = Field(
                default=None, description="Child nodes"
            )

        # Required for forward references in Pydantic
        TreeNode.model_rebuild()

        # Verify the generated schema has circular $ref
        schema = TreeNode.model_json_schema()
        assert "$defs" in schema
        assert "TreeNode" in schema["$defs"]
        # The TreeNode def should reference itself via $ref
        tree_def = schema["$defs"]["TreeNode"]
        assert "children" in tree_def["properties"]

        # This should NOT raise RecursionError - the fix handles circular refs
        result = TreeNode.to_mcp_schema()

        # Verify the result is valid
        assert result["type"] == "object"
        assert "properties" in result
        assert "value" in result["properties"]
        # The children property should be present (as an array of generic objects)
        assert "children" in result["properties"]
        json.dumps(result)  # Should not raise

    def test_deeply_nested_non_circular_schema_works(self):
        """Test that deeply nested but non-circular schemas work correctly.

        This ensures we don't break valid deeply nested schemas while fixing
        the circular reference issue.
        """
        # A deeply nested but non-circular schema
        deep_schema = {
            "type": "object",
            "properties": {
                "level1": {
                    "type": "object",
                    "properties": {
                        "level2": {
                            "type": "object",
                            "properties": {
                                "level3": {
                                    "type": "object",
                                    "properties": {
                                        "value": {"type": "string"},
                                    },
                                }
                            },
                        }
                    },
                }
            },
        }

        # This should work without errors
        result = _process_schema_node(deep_schema, {})
        assert result["type"] == "object"
        assert "properties" in result

        # Should be JSON serializable
        json.dumps(result)

    def test_non_circular_ref_schema_works(self):
        """Test that schemas with non-circular $ref work correctly.

        Some schemas have $ref but they don't form a cycle. These should
        still work correctly.
        """
        schema = {
            "type": "object",
            "properties": {
                "address": {"$ref": "#/$defs/Address"},
            },
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                    },
                }
            },
        }

        defs = schema.get("$defs", {})
        result = _process_schema_node(schema, defs)

        # Should resolve the $ref correctly
        assert result["type"] == "object"
        assert "properties" in result
        assert "address" in result["properties"]
        # The address should be resolved to the actual definition
        assert result["properties"]["address"]["type"] == "object"

        # Should be JSON serializable
        json.dumps(result)

    def test_circular_ref_detection_stops_recursion(self):
        """Test that circular $ref is detected and handled gracefully.

        After the fix, _process_schema_node should detect circular references
        and handle them without infinite recursion. The fix should either:
        1. Leave circular $ref as-is (don't try to inline it)
        2. Replace with a generic object type
        3. Track visited refs and skip already-seen ones

        This test will PASS once the fix is implemented.
        """
        circular_schema = {
            "type": "object",
            "properties": {
                "children": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Node"},
                },
            },
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Node"},
                        },
                    },
                }
            },
        }

        defs = circular_schema.get("$defs", {})

        # After fix: This should NOT raise RecursionError
        # Instead it should return a valid schema
        try:
            result = _process_schema_node(circular_schema, defs)
            # Verify the result is JSON serializable (no circular Python refs)
            json.dumps(result)
        except RecursionError:
            pytest.fail(
                "RecursionError still occurs - fix not yet implemented. "
                "_process_schema_node needs circular reference detection."
            )

    def test_self_referential_pydantic_schema_to_mcp_works(self):
        """Test that self-referential Pydantic Schema can be converted to MCP.

        After the fix, a Schema with self-referential fields should be able
        to call to_mcp_schema() without hitting RecursionError.
        """

        class LinkedListNode(Schema):
            """A linked list node with optional next pointer."""

            value: int = Field(description="The value")
            next: "LinkedListNode | None" = Field(default=None, description="Next node")

        LinkedListNode.model_rebuild()

        # After fix: This should work without RecursionError
        try:
            result = LinkedListNode.to_mcp_schema()
            # Should be JSON serializable
            json.dumps(result)
            # Should have the expected structure
            assert result["type"] == "object"
            assert "value" in result.get("properties", {})
        except RecursionError:
            pytest.fail(
                "RecursionError in to_mcp_schema() - fix not yet implemented. "
                "_process_schema_node needs circular reference detection."
            )
