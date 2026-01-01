"""Tests for MCP schema generation in openhands.sdk.tool.schema."""

from collections.abc import Sequence

from pydantic import Field

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool.schema import Action, Observation


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
