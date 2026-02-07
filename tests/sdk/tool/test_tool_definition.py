"""Tests for the Tool class in openhands.sdk.runtime.tool."""

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import Field

from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


class ToolMockAction(Action):
    """Mock action class for testing."""

    command: str = Field(description="Command to execute")
    optional_field: str | None = Field(default=None, description="Optional field")
    nested: dict[str, Any] = Field(default_factory=dict, description="Nested object")
    array_field: list[int] = Field(default_factory=list, description="Array field")


# Module-level Action classes to avoid "local class not supported" errors
# during serialization tests. Local classes (defined inside functions) cannot be
# deserialized because they may not exist at deserialization time.
class ComplexSchemaAction(Action):
    """Action with complex field types for schema generation testing."""

    simple_field: str = Field(description="Simple string field")
    optional_int: int | None = Field(default=None, description="Optional integer")
    string_list: list[str] = Field(default_factory=list, description="List of strings")


class RequiredFieldAction(Action):
    """Action with required and optional fields for testing."""

    required_field: str = Field(description="This field is required")
    optional_field: str | None = Field(
        default=None, description="This field is optional"
    )


class ComplexNestedAction(Action):
    """Action with complex nested types for testing."""

    simple_string: str = Field(description="Simple string field")
    optional_int: int | None = Field(default=None, description="Optional integer")
    string_array: list[str] = Field(
        default_factory=list, description="Array of strings"
    )
    int_array: list[int] = Field(default_factory=list, description="Array of integers")
    nested_dict: dict[str, Any] = Field(
        default_factory=dict, description="Nested dictionary"
    )
    optional_array: list[str | None] | None = Field(
        default=None, description="Optional array"
    )


class ToolMockObservation(Observation):
    """Mock observation class for testing."""

    result: str = Field(description="Result of the action")
    extra_field: str | None = Field(default=None, description="Extra field")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


class ComplexObservation(Observation):
    """Observation with complex data for testing."""

    data: dict[str, Any] = Field(default_factory=dict, description="Complex data")
    count: int = Field(default=0, description="Count field")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=f"Data: {self.data}, Count: {self.count}")]


class RequiredFieldsObservation(Observation):
    """Observation with required fields for validation testing.

    Note: Defined at module level to ensure a stable qualified name for
    JSON serialization/deserialization.
    """

    message: str = Field(description="Required message field")
    value: int = Field(description="Required value field")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=f"{self.message}: {self.value}")]


class MockTestTool(ToolDefinition[ToolMockAction, ToolMockObservation]):
    """Concrete mock tool for testing."""

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockTestTool"]:
        return [cls(**params)]


class TestTool:
    """Test cases for the Tool class."""

    def test_tool_creation_basic(self):
        """Test basic tool creation."""
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        assert tool.name == "mock_test"
        assert tool.description == "A test tool"
        assert tool.action_type == ToolMockAction
        assert tool.observation_type == ToolMockObservation
        assert tool.executor is None

    def test_tool_creation_with_executor(self):
        """Test tool creation with executor function."""

        class MockExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                return ToolMockObservation(result=f"Executed: {action.command}")

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=MockExecutor(),
        )

        # Test that tool can be used as executable
        executable_tool = tool.as_executable()
        action = ToolMockAction(command="test")
        result = executable_tool(action)
        assert isinstance(result, ToolMockObservation)
        assert result.result == "Executed: test"

    def test_tool_creation_with_annotations(self):
        """Test tool creation with annotations."""
        annotations = ToolAnnotations(
            title="Annotated Tool",
            readOnlyHint=True,
            destructiveHint=False,
        )

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            annotations=annotations,
        )

        assert tool.annotations is not None
        assert tool.annotations == annotations
        assert tool.annotations.title == "Annotated Tool"
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False

    def test_to_mcp_tool_basic(self):
        """Test conversion to MCP tool format."""
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        mcp_tool = tool.to_mcp_tool()

        assert mcp_tool["name"] == "mock_test"
        assert mcp_tool["description"] == "A test tool"
        assert "inputSchema" in mcp_tool
        assert mcp_tool["inputSchema"]["type"] == "object"
        assert "properties" in mcp_tool["inputSchema"]

        # Check that action fields are in the schema
        properties = mcp_tool["inputSchema"]["properties"]
        assert "command" in properties
        assert "optional_field" in properties
        assert "nested" in properties
        assert "array_field" in properties

    def test_to_mcp_tool_with_annotations(self):
        """Test MCP tool conversion with annotations."""
        annotations = ToolAnnotations(
            title="Custom Tool",
            readOnlyHint=True,
        )

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            annotations=annotations,
        )

        mcp_tool = tool.to_mcp_tool()

        # Tool should include annotations
        assert mcp_tool["name"] == "mock_test"
        assert mcp_tool["description"] == "A test tool"
        assert "annotations" in mcp_tool
        assert mcp_tool["annotations"] == annotations

    def test_call_without_executor(self):
        """Test calling tool without executor raises error."""
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        action = ToolMockAction(command="test")
        with pytest.raises(
            NotImplementedError, match="Tool 'mock_test' has no executor"
        ):
            tool(action)

    def test_call_with_executor(self):
        """Test calling tool with executor."""

        class MockExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                return ToolMockObservation(result=f"Processed: {action.command}")

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=MockExecutor(),
        )

        action = ToolMockAction(command="test_command")
        result = tool(action)

        assert isinstance(result, ToolMockObservation)
        assert result.result == "Processed: test_command"

    def test_schema_generation_complex_types(self):
        """Test schema generation with complex field types."""
        tool = MockTestTool(
            description="Tool with complex types",
            action_type=ComplexSchemaAction,
            observation_type=ToolMockObservation,
        )

        mcp_tool = tool.to_mcp_tool()
        properties = mcp_tool["inputSchema"]["properties"]
        assert "simple_field" in properties
        assert properties["simple_field"]["type"] == "string"
        assert "optional_int" in properties
        assert properties["optional_int"]["type"] == "integer"
        assert "string_list" in properties
        assert properties["string_list"]["type"] == "array"
        assert properties["string_list"]["items"]["type"] == "string"

    def test_observation_type_validation(self):
        """Test that observation type is properly validated."""

        class MockExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                return ToolMockObservation(result="success")

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=MockExecutor(),
        )

        action = ToolMockAction(command="test")
        result = tool(action)

        # Should return the correct observation type
        assert isinstance(result, ToolMockObservation)
        assert result.result == "success"

    def test_observation_with_extra_fields(self):
        """Test observation with additional fields."""

        class MockExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                return ToolMockObservation(result="test", extra_field="extra_data")

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=MockExecutor(),
        )

        action = ToolMockAction(command="test")
        result = tool(action)

        assert isinstance(result, ToolMockObservation)
        assert result.result == "test"
        assert result.extra_field == "extra_data"

    def test_action_validation_with_nested_data(self):
        """Test action validation with nested data structures."""
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        # Create action with nested data
        action_data = {
            "command": "test",
            "nested": {"value": "test"},
            "array_field": [1, 2, 3],
        }
        action = tool.action_type.model_validate(action_data)

        assert isinstance(action, ToolMockAction)
        assert action.nested == {"value": "test"}
        assert action.array_field == [1, 2, 3]
        assert hasattr(action, "optional_field")

    def test_schema_roundtrip_conversion(self):
        """Test that schema conversion is consistent."""
        # Start with a class
        original_schema = ToolMockAction.to_mcp_schema()

        # Create tool and get its schema
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )
        tool_schema = tool.to_mcp_tool()["inputSchema"]

        # Schemas should be equivalent (ignoring order)
        assert original_schema["type"] == tool_schema["type"]
        assert set(original_schema["properties"].keys()) == set(
            tool_schema["properties"].keys()
        )

    def test_tool_with_no_observation_type(self):
        """Test tool creation with None observation type."""
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=None,
        )

        assert tool.observation_type is None

        # Should still be able to create MCP tool
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool["name"] == "mock_test"

    def test_executor_function_attachment(self):
        """Test creating tool with executor."""

        # Create executor first
        class MockExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                return ToolMockObservation(result=f"Attached: {action.command}")

        executor = MockExecutor()

        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=executor,
        )

        # Should work as executable tool
        executable_tool = tool.as_executable()
        action = ToolMockAction(command="test")
        result = executable_tool(action)
        assert isinstance(result, ToolMockObservation)
        assert result.result == "Attached: test"

    def test_tool_name_validation(self):
        """Test tool name validation."""
        # Name is now automatically generated from class name
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )
        assert tool.name == "mock_test"

    def test_complex_executor_return_types(self):
        """Test executor with complex return types."""

        class MockComplexExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ComplexObservation:
                return ComplexObservation(
                    data={"processed": action.command, "timestamp": 12345},
                    count=len(action.command) if hasattr(action, "command") else 0,
                )

        tool = MockTestTool(
            description="Tool with complex observation",
            action_type=ToolMockAction,
            observation_type=ComplexObservation,
            executor=MockComplexExecutor(),
        )

        action = ToolMockAction(command="test_command")
        result = tool(action)

        assert isinstance(result, ComplexObservation)
        assert result.data["processed"] == "test_command"
        assert result.count == len("test_command")

    def test_error_handling_in_executor(self):
        """Test error handling when executor raises exceptions."""

        class FailingExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                raise RuntimeError("Executor failed")

        tool = MockTestTool(
            description="Tool that fails",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=FailingExecutor(),
        )

        action = ToolMockAction(command="test")
        with pytest.raises(RuntimeError, match="Executor failed"):
            tool(action)

    def test_executor_with_observation_validation(self):
        """Test that executor return values are validated."""

        class ValidExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> RequiredFieldsObservation:
                return RequiredFieldsObservation(message="success", value=42)

        tool = MockTestTool(
            description="Tool with required fields observation",
            action_type=ToolMockAction,
            observation_type=RequiredFieldsObservation,
            executor=ValidExecutor(),
        )

        action = ToolMockAction(command="test")
        result = tool(action)
        assert isinstance(result, RequiredFieldsObservation)
        assert result.message == "success"
        assert result.value == 42

    def test_tool_equality_and_hashing(self):
        """Test tool equality and hashing behavior."""
        tool1 = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        tool2 = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        # Tools with same parameters should be equal
        assert tool1.name == tool2.name
        assert tool1.description == tool2.description
        assert tool1.action_type == tool2.action_type

    def test_mcp_tool_schema_required_fields(self):
        """Test that MCP tool schema includes required fields."""
        tool = MockTestTool(
            description="Tool with required fields",
            action_type=RequiredFieldAction,
            observation_type=ToolMockObservation,
        )

        mcp_tool = tool.to_mcp_tool()
        schema = mcp_tool["inputSchema"]

        # Check that required fields are marked as required
        assert "required" in schema
        assert "required_field" in schema["required"]
        assert "optional_field" not in schema["required"]

    def test_tool_with_meta_data(self):
        """Test tool creation with metadata."""
        meta_data = {"version": "1.0", "author": "test"}

        tool = MockTestTool(
            description="Tool with metadata",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            meta=meta_data,
        )

        assert tool.meta == meta_data

        mcp_tool = tool.to_mcp_tool()
        assert "_meta" in mcp_tool
        assert mcp_tool["_meta"] == meta_data

    def test_to_mcp_tool_complex_nested_types(self):
        """Test MCP tool schema generation with complex nested types."""
        tool = MockTestTool(
            description="Tool with complex nested types",
            action_type=ComplexNestedAction,
            observation_type=ToolMockObservation,
        )

        mcp_tool = tool.to_mcp_tool()
        schema = mcp_tool["inputSchema"]
        props = schema["properties"]

        # Test simple string
        assert props["simple_string"]["type"] == "string"
        assert "simple_string" in schema["required"]

        # Test optional int
        optional_int_schema = props["optional_int"]
        assert "anyOf" not in optional_int_schema
        assert optional_int_schema["type"] == "integer"
        assert "optional_int" not in schema["required"]

        # Test string array
        string_array_schema = props["string_array"]
        assert string_array_schema["type"] == "array"
        assert string_array_schema["items"]["type"] == "string"

        # Test int array
        int_array_schema = props["int_array"]
        assert int_array_schema["type"] == "array"
        assert int_array_schema["items"]["type"] == "integer"

        # Test nested dict
        nested_dict_schema = props["nested_dict"]
        assert nested_dict_schema["type"] == "object"

        # Test optional array
        optional_array_schema = props["optional_array"]
        assert "anyOf" not in optional_array_schema
        assert optional_array_schema["type"] == "array"
        assert optional_array_schema["items"]["type"] == "string"

    def test_security_risk_only_added_for_non_readonly_tools(self):
        """Test that security_risk is only added if the tool is not read-only."""
        # Test with read-only tool
        readonly_annotations = ToolAnnotations(
            title="Read-only Tool",
            readOnlyHint=True,
        )

        readonly_tool = MockTestTool(
            description="A read-only tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            annotations=readonly_annotations,
        )

        # Test with non-read-only tool
        writable_annotations = ToolAnnotations(
            title="Writable Tool",
            readOnlyHint=False,
        )

        writable_tool = MockTestTool(
            description="A writable tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            annotations=writable_annotations,
        )

        # Test with tool that has no annotations (should be treated as writable)
        no_annotations_tool = MockTestTool(
            description="A tool with no annotations",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            annotations=None,
        )

        # Test read-only tool - security_risk should NOT be added
        readonly_openai_tool = readonly_tool.to_openai_tool(
            add_security_risk_prediction=True
        )
        readonly_function = readonly_openai_tool["function"]
        assert "parameters" in readonly_function
        readonly_params = readonly_function["parameters"]
        assert "security_risk" not in readonly_params["properties"]

        # Test writable tool - security_risk SHOULD be added
        writable_openai_tool = writable_tool.to_openai_tool(
            add_security_risk_prediction=True
        )
        writable_function = writable_openai_tool["function"]
        assert "parameters" in writable_function
        writable_params = writable_function["parameters"]
        assert "security_risk" in writable_params["properties"]

        # Test tool with no annotations - security_risk SHOULD be added
        no_annotations_openai_tool = no_annotations_tool.to_openai_tool(
            add_security_risk_prediction=True
        )
        no_annotations_function = no_annotations_openai_tool["function"]
        assert "parameters" in no_annotations_function
        no_annotations_params = no_annotations_function["parameters"]
        assert "security_risk" in no_annotations_params["properties"]

        # Test that when add_security_risk_prediction=False, no security_risk is added
        readonly_no_risk = readonly_tool.to_openai_tool(
            add_security_risk_prediction=False
        )
        readonly_no_risk_function = readonly_no_risk["function"]
        assert "parameters" in readonly_no_risk_function
        readonly_no_risk_params = readonly_no_risk_function["parameters"]
        assert "security_risk" not in readonly_no_risk_params["properties"]

        writable_no_risk = writable_tool.to_openai_tool(
            add_security_risk_prediction=False
        )
        writable_no_risk_function = writable_no_risk["function"]
        assert "parameters" in writable_no_risk_function
        writable_no_risk_params = writable_no_risk_function["parameters"]
        assert "security_risk" not in writable_no_risk_params["properties"]

    def test_security_risk_is_required_field_in_schema(self):
        """Test that _create_action_type_with_risk always makes security_risk a required field."""  # noqa: E501
        from openhands.sdk.tool.tool import create_action_type_with_risk

        # Test with a simple action type
        action_type_with_risk = create_action_type_with_risk(ToolMockAction)

        # Get the schema and check that security_risk is in required fields
        schema = action_type_with_risk.to_mcp_schema()
        assert "security_risk" in schema["properties"]
        assert "security_risk" in schema["required"]

        # Test via to_openai_tool method
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        openai_tool = tool.to_openai_tool(add_security_risk_prediction=True)
        function_chunk = openai_tool["function"]
        assert "parameters" in function_chunk
        function_params = function_chunk["parameters"]

        # Verify security_risk is present in properties
        assert "security_risk" in function_params["properties"]

        # Verify security_risk is in the required fields list
        assert "security_risk" in function_params["required"]

        # Test with a tool that has annotations but is not read-only
        writable_annotations = ToolAnnotations(
            title="Writable Tool",
            readOnlyHint=False,
        )

        writable_tool = MockTestTool(
            description="A writable tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            annotations=writable_annotations,
        )

        writable_openai_tool = writable_tool.to_openai_tool(
            add_security_risk_prediction=True
        )
        writable_function_chunk = writable_openai_tool["function"]
        assert "parameters" in writable_function_chunk
        writable_function_params = writable_function_chunk["parameters"]

        # Verify security_risk is present and required
        assert "security_risk" in writable_function_params["properties"]
        assert "security_risk" in writable_function_params["required"]

    def test_as_executable_with_executor(self):
        """Test as_executable() method with a tool that has an executor."""

        class MockExecutor(ToolExecutor):
            def __call__(self, action, conversation=None) -> ToolMockObservation:
                return ToolMockObservation(result=f"Executed: {action.command}")

        executor = MockExecutor()
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
            executor=executor,
        )

        # Should return ExecutableTool without error
        executable_tool = tool.as_executable()
        assert executable_tool.name == "mock_test"
        assert executable_tool.executor is executor

        # Should be able to call it
        action = ToolMockAction(command="test")
        result = executable_tool(action)
        assert isinstance(result, ToolMockObservation)
        assert result.result == "Executed: test"

    def test_as_executable_without_executor(self):
        """Test as_executable() method with a tool that has no executor."""
        tool = MockTestTool(
            description="A test tool",
            action_type=ToolMockAction,
            observation_type=ToolMockObservation,
        )

        # Should raise NotImplementedError
        with pytest.raises(
            NotImplementedError, match="Tool 'mock_test' has no executor"
        ):
            tool.as_executable()
