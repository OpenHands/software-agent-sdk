"""Tests for schema immutability in openhands.sdk.tool.schema."""

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import Field, ValidationError

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.mcp.definition import MCPToolAction
from openhands.sdk.tool.schema import (
    Action,
    Observation,
    Schema,
)


class MockSchema(Schema):
    """Mock schema class for testing."""

    name: str = Field(description="Name field")
    value: int = Field(description="Value field")
    optional_field: str | None = Field(default=None, description="Optional field")


class SchemaImmutabilityMockAction(Action):
    """Mock action class for testing."""

    command: str = Field(description="Command to execute")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata")


class MockMCPAction(MCPToolAction):
    """Mock MCP action class for testing."""

    operation: str = Field(description="Operation to perform")
    parameters: dict[str, str] = Field(
        default_factory=dict, description="Operation parameters"
    )


class SchemaImmutabilityMockObservation(Observation):
    """Mock observation class for testing."""

    result: str = Field(description="Result of the action")
    status: str = Field(default="success", description="Status of the operation")
    data: dict[str, Any | None] | None = Field(default=None, description="Result data")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Get the observation string to show to the agent."""
        return [TextContent(text=f"Result: {self.result}, Status: {self.status}")]


class _SchemaImmutabilityCustomAction(Action):
    """Custom action for testing schema inheritance immutability.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

    custom_field: str = Field(description="Custom field")


class _SchemaImmutabilityCustomObservation(Observation):
    """Custom observation for testing schema inheritance immutability.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

    custom_result: str = Field(description="Custom result")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.custom_result)]


def test_schema_is_frozen():
    """Test that Schema instances are frozen and cannot be modified."""
    schema = MockSchema(name="test", value=42)

    # Test that we cannot modify any field
    with pytest.raises(ValidationError, match="Instance is frozen"):
        schema.name = "modified"

    with pytest.raises(ValidationError, match="Instance is frozen"):
        schema.value = 100

    with pytest.raises(ValidationError, match="Instance is frozen"):
        schema.optional_field = "new_value"


def test_action_base_is_frozen():
    """Test that Action instances are frozen and cannot be modified."""
    action = SchemaImmutabilityMockAction(command="test_command", args=["arg1", "arg2"])

    # Test that we cannot modify any field
    with pytest.raises(ValidationError, match="Instance is frozen"):
        action.command = "modified_command"

    with pytest.raises(ValidationError, match="Instance is frozen"):
        action.args = ["new_arg"]

    with pytest.raises(ValidationError, match="Instance is frozen"):
        action.metadata = {"new": "data"}


def test_mcp_action_base_is_frozen():
    """Test that MCPToolAction instances are frozen and cannot be modified."""
    action = MockMCPAction(operation="test_op", parameters={"key": "value"})

    # Test that we cannot modify any field
    with pytest.raises(ValidationError, match="Instance is frozen"):
        action.operation = "modified_op"

    with pytest.raises(ValidationError, match="Instance is frozen"):
        action.parameters = {"new": "params"}


def test_observation_base_is_frozen():
    """Test that Observation instances are frozen and cannot be modified."""
    observation = SchemaImmutabilityMockObservation(
        result="test_result", status="completed"
    )

    # Test that we cannot modify any field
    with pytest.raises(ValidationError, match="Instance is frozen"):
        observation.result = "modified_result"

    with pytest.raises(ValidationError, match="Instance is frozen"):
        observation.status = "failed"

    with pytest.raises(ValidationError, match="Instance is frozen"):
        observation.data = {"new": "data"}


def test_schema_model_copy_creates_new_instance():
    """Test that model_copy creates a new instance with updated fields."""
    original = MockSchema(name="original", value=10)

    # Create a copy with updated fields
    updated = original.model_copy(update={"name": "updated", "value": 20})

    # Verify original is unchanged
    assert original.name == "original"
    assert original.value == 10

    # Verify updated instance has new values
    assert updated.name == "updated"
    assert updated.value == 20

    # Verify they are different instances
    assert original is not updated


def test_action_model_copy_creates_new_instance():
    """Test that Action model_copy creates a new instance with updated fields."""
    original = SchemaImmutabilityMockAction(command="original_cmd", args=["arg1"])

    # Create a copy with updated fields
    updated = original.model_copy(
        update={"command": "updated_cmd", "args": ["arg1", "arg2"]}
    )

    # Verify original is unchanged
    assert original.command == "original_cmd"
    assert original.args == ["arg1"]

    # Verify updated instance has new values
    assert updated.command == "updated_cmd"
    assert updated.args == ["arg1", "arg2"]

    # Verify they are different instances
    assert original is not updated


def test_mcp_action_model_copy_creates_new_instance():
    """Test that MCPToolAction model_copy creates a new instance with updated fields."""
    original = MockMCPAction(operation="original_op", parameters={"key": "value"})

    # Create a copy with updated fields
    updated = original.model_copy(
        update={"operation": "updated_op", "parameters": {"new_key": "new_value"}}
    )

    # Verify original is unchanged
    assert original.operation == "original_op"
    assert original.parameters == {"key": "value"}

    # Verify updated instance has new values
    assert updated.operation == "updated_op"
    assert updated.parameters == {"new_key": "new_value"}

    # Verify they are different instances
    assert original is not updated


def test_observation_model_copy_creates_new_instance():
    """Test that Observation model_copy creates a new instance.

    Creates a new instance with updated fields.
    """
    original = SchemaImmutabilityMockObservation(
        result="original_result", status="pending"
    )

    # Create a copy with updated fields
    updated = original.model_copy(
        update={"result": "updated_result", "status": "completed"}
    )

    # Verify original is unchanged
    assert original.result == "original_result"
    assert original.status == "pending"

    # Verify updated instance has new values
    assert updated.result == "updated_result"
    assert updated.status == "completed"

    # Verify they are different instances
    assert original is not updated


def test_schema_immutability_prevents_mutation_bugs():
    """Test a practical scenario where immutability prevents mutation bugs."""
    # Create an action that might be shared across multiple contexts
    shared_action = SchemaImmutabilityMockAction(
        command="shared_cmd", args=["shared_arg"]
    )

    # Simulate two different contexts trying to modify the action
    def context_a_processing(
        action: SchemaImmutabilityMockAction,
    ) -> SchemaImmutabilityMockAction:
        # Context A wants to reassign the args field - this should fail
        with pytest.raises(ValidationError, match="Instance is frozen"):
            action.args = action.args + ["context_a_arg"]

        # Context A should use model_copy instead
        return action.model_copy(update={"args": action.args + ["context_a_arg"]})

    def context_b_processing(
        action: SchemaImmutabilityMockAction,
    ) -> SchemaImmutabilityMockAction:
        # Context B wants to change the command - this should fail
        with pytest.raises(ValidationError, match="Instance is frozen"):
            action.command = "context_b_cmd"

        # Context B should use model_copy instead
        return action.model_copy(update={"command": "context_b_cmd"})

    # Process the action in both contexts
    action_a = context_a_processing(shared_action)
    action_b = context_b_processing(shared_action)

    # Verify the original action is unchanged
    assert shared_action.command == "shared_cmd"
    assert shared_action.args == ["shared_arg"]

    # Verify each context got its own modified version
    assert action_a.command == "shared_cmd"
    assert action_a.args == ["shared_arg", "context_a_arg"]

    assert action_b.command == "context_b_cmd"
    assert action_b.args == ["shared_arg"]

    # Verify all instances are different
    assert shared_action is not action_a
    assert shared_action is not action_b
    assert action_a is not action_b


def test_all_schema_classes_are_frozen():
    """Test that all schema base classes are properly frozen."""
    # Test Schema
    schema = MockSchema(name="test", value=1)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        schema.name = "changed"

    # Test Action
    action = SchemaImmutabilityMockAction(command="test")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        action.command = "changed"

    # Test MCPToolAction
    mcp_action = MockMCPAction(operation="test")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        mcp_action.operation = "changed"

    # Test Observation
    observation = SchemaImmutabilityMockObservation(result="test")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        observation.result = "changed"


def test_schema_inheritance_preserves_immutability():
    """Test that classes inheriting from schema bases are also immutable."""
    # Test that custom classes are also frozen
    custom_action = _SchemaImmutabilityCustomAction(custom_field="test")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        custom_action.custom_field = "changed"

    custom_obs = _SchemaImmutabilityCustomObservation(custom_result="test")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        custom_obs.custom_result = "changed"
