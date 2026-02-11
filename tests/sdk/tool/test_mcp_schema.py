"""Tests for MCP schema generation in openhands.sdk.tool.schema."""

import json
from collections.abc import Sequence

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

    These tests verify that circular schemas are handled gracefully without
    RecursionError. When a circular reference is detected, a generic
    {"type": "object"} placeholder is returned.

    Related: Datadog logs from conversation ab9909a07571431a86ab6f1be36f555f
    """

    def test_circular_ref_returns_generic_object(self):
        """Test that circular ref handling returns a generic object.

        When a circular reference is detected, the function returns a simple
        {"type": "object"} placeholder to prevent infinite recursion.
        """
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
                    "description": "A tree node",
                    "properties": {
                        "name": {"type": "string", "description": "Node name"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/TreeNode"},
                            "description": "Child nodes",
                        },
                    },
                }
            },
        }

        defs = circular_schema.get("$defs", {})
        result = _process_schema_node(circular_schema, defs)

        # Verify basic structure
        assert result["type"] == "object"
        assert "properties" in result

        # The top-level 'name' should be preserved
        assert result["properties"]["name"]["type"] == "string"

        # The 'children' array should be present
        assert result["properties"]["children"]["type"] == "array"

        # The items in children should be expanded TreeNodes (first level)
        items = result["properties"]["children"]["items"]
        assert items["type"] == "object"
        assert "properties" in items

        # The TreeNode's 'name' property should be preserved (first level)
        assert "name" in items["properties"]
        assert items["properties"]["name"]["type"] == "string"

        # The TreeNode's 'children' should be an array
        assert "children" in items["properties"]
        assert items["properties"]["children"]["type"] == "array"

        # The nested items (circular ref) should be a generic object
        nested_items = items["properties"]["children"]["items"]
        assert nested_items["type"] == "object"
        # Description is preserved from the ref definition
        assert nested_items.get("description") == "A tree node"

        # Should be JSON serializable
        json.dumps(result)

    def test_tree_schema_to_mcp_works(self):
        """Test that self-referential Pydantic Schema can be converted to MCP schema.

        This is the real-world scenario: a Pydantic model with self-referential
        fields (like a tree node) should be convertible without RecursionError.
        """

        class TreeNode(Schema):
            """A tree node that can have children of the same type."""

            value: str = Field(description="The value of this node")
            children: list["TreeNode"] | None = Field(
                default=None, description="Child nodes"
            )

        TreeNode.model_rebuild()

        result = TreeNode.to_mcp_schema()

        # Verify the result structure
        assert result["type"] == "object"
        assert "properties" in result

        # The 'value' field should be fully preserved
        assert "value" in result["properties"]
        assert result["properties"]["value"]["type"] == "string"
        assert result["properties"]["value"]["description"] == "The value of this node"

        # The 'children' field should be present as an array
        assert "children" in result["properties"]
        children_prop = result["properties"]["children"]
        assert children_prop["type"] == "array"

        # The items should be objects (circular ref returns generic object)
        assert children_prop["items"]["type"] == "object"

        # Should be JSON serializable
        json.dumps(result)

    def test_deeply_nested_non_circular_schema_fully_resolved(self):
        """Test that deeply nested but non-circular schemas are fully resolved.

        This ensures we don't break valid deeply nested schemas while fixing
        the circular reference issue.
        """
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

        result = _process_schema_node(deep_schema, {})

        # Verify full nesting is preserved
        assert result["type"] == "object"
        level1 = result["properties"]["level1"]
        assert level1["type"] == "object"
        level2 = level1["properties"]["level2"]
        assert level2["type"] == "object"
        level3 = level2["properties"]["level3"]
        assert level3["type"] == "object"
        assert level3["properties"]["value"]["type"] == "string"

        json.dumps(result)

    def test_non_circular_ref_fully_resolved(self):
        """Test that schemas with non-circular $ref are fully resolved."""
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

        # Should resolve the $ref completely
        assert result["type"] == "object"
        address = result["properties"]["address"]
        assert address["type"] == "object"
        assert address["properties"]["street"]["type"] == "string"
        assert address["properties"]["city"]["type"] == "string"

        json.dumps(result)

    def test_circular_ref_does_not_raise_recursion_error(self):
        """Test that circular $ref does not cause RecursionError."""
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

        # Should not raise RecursionError
        result = _process_schema_node(circular_schema, defs)

        # Verify valid output
        assert result["type"] == "object"
        assert "properties" in result
        json.dumps(result)

    def test_linked_list_schema_to_mcp_works(self):
        """Test that linked list Schema can be converted to MCP schema."""

        class LinkedListNode(Schema):
            """A linked list node with optional next pointer."""

            value: int = Field(description="The value")
            next: "LinkedListNode | None" = Field(default=None, description="Next node")

        LinkedListNode.model_rebuild()

        result = LinkedListNode.to_mcp_schema()

        # Verify structure
        assert result["type"] == "object"
        assert "value" in result["properties"]
        assert result["properties"]["value"]["type"] == "integer"
        assert result["properties"]["value"]["description"] == "The value"

        # 'next' should be present (as a simplified object)
        assert "next" in result["properties"]
        assert result["properties"]["next"]["type"] == "object"

        json.dumps(result)
