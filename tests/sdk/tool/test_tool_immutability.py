"""Tests for the Tool class in openhands.sdk.runtime.tool."""

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import Field, ValidationError

from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


class ToolImmutabilityMockAction(Action):
    """Mock action class for testing."""

    command: str = Field(description="Command to execute")
    optional_field: str | None = Field(default=None, description="Optional field")
    nested: dict[str, Any] = Field(default_factory=dict, description="Nested object")
    array_field: list[int] = Field(default_factory=list, description="Array field")


class ToolImmutabilityMockObservation(Observation):
    """Mock observation class for testing."""

    result: str = Field(description="Result of the action")
    extra_field: str | None = Field(default=None, description="Extra field")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


class MockImmutableTool(
    ToolDefinition[ToolImmutabilityMockAction, ToolImmutabilityMockObservation]
):
    """Concrete mock tool for immutability testing."""

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockImmutableTool"]:
        return [cls(**params)]


class TestToolImmutability:
    """Test suite for Tool immutability features."""

    def test_tool_is_frozen(self):
        """Test that Tool instances are frozen and cannot be modified."""
        tool = MockImmutableTool(
            description="Test tool",
            action_type=ToolImmutabilityMockAction,
            observation_type=ToolImmutabilityMockObservation,
        )

        # Test that we cannot modify any field
        # Note: name is now a ClassVar and cannot be assigned through instance
        with pytest.raises(Exception):
            tool.description = "modified_description"

        with pytest.raises(Exception):
            tool.executor = None

    def test_tool_set_executor_returns_new_instance(self):
        """Test that set_executor returns a new Tool instance."""
        tool = MockImmutableTool(
            description="Test tool",
            action_type=ToolImmutabilityMockAction,
            observation_type=ToolImmutabilityMockObservation,
        )

        class NewExecutor(
            ToolExecutor[ToolImmutabilityMockAction, ToolImmutabilityMockObservation]
        ):
            def __call__(
                self, action: ToolImmutabilityMockAction, conversation=None
            ) -> ToolImmutabilityMockObservation:
                return ToolImmutabilityMockObservation(result="new_result")

        new_executor = NewExecutor()
        new_tool = tool.set_executor(new_executor)

        # Verify that a new instance was created
        assert new_tool is not tool
        assert tool.executor is None
        assert new_tool.executor is new_executor
        assert new_tool.name == tool.name
        assert new_tool.description == tool.description

    def test_tool_model_copy_creates_modified_instance(self):
        """Test that model_copy can create modified versions of Tool instances."""
        tool = MockImmutableTool(
            description="Test tool",
            action_type=ToolImmutabilityMockAction,
            observation_type=ToolImmutabilityMockObservation,
        )

        # Create a copy with modified fields
        modified_tool = tool.model_copy(
            update={"name": "modified_tool", "description": "Modified description"}
        )

        # Verify that a new instance was created with modifications
        assert modified_tool is not tool
        assert tool.name == "mock_immutable"
        assert tool.description == "Test tool"
        assert modified_tool.name == "modified_tool"
        assert modified_tool.description == "Modified description"

    def test_tool_meta_field_immutability(self):
        """Test that the meta field works correctly and is immutable."""
        meta_data = {"version": "1.0", "author": "test"}
        tool = MockImmutableTool(
            description="Test tool",
            action_type=ToolImmutabilityMockAction,
            observation_type=ToolImmutabilityMockObservation,
            meta=meta_data,
        )

        # Verify meta field is accessible
        assert tool.meta == meta_data

        # Test that meta field cannot be directly modified
        with pytest.raises(Exception):
            tool.meta = {"version": "2.0"}

        # Test that meta field can be modified via model_copy
        new_meta = {"version": "2.0", "author": "new_author"}
        modified_tool = tool.model_copy(update={"meta": new_meta})
        assert modified_tool.meta == new_meta
        assert tool.meta == meta_data  # Original unchanged

    def test_tool_constructor_parameter_validation(self):
        """Test that Tool constructor validates parameters correctly."""
        # Test that new parameter names work
        tool = MockImmutableTool(
            description="Test tool",
            action_type=ToolImmutabilityMockAction,
            observation_type=ToolImmutabilityMockObservation,
        )
        assert tool.action_type == ToolImmutabilityMockAction
        assert tool.observation_type == ToolImmutabilityMockObservation

        # Test that invalid field types are rejected
        with pytest.raises(ValidationError):
            MockImmutableTool(
                description="Test tool",
                action_type="invalid_type",  # type: ignore[arg-type] # Should be a class, not string
                observation_type=ToolImmutabilityMockObservation,
            )

    def test_tool_annotations_immutability(self):
        """Test that ToolAnnotations are also immutable when part of Tool."""
        annotations = ToolAnnotations(
            title="Test Tool",
            readOnlyHint=True,
            destructiveHint=False,
        )

        tool = MockImmutableTool(
            description="Test tool",
            action_type=ToolImmutabilityMockAction,
            observation_type=ToolImmutabilityMockObservation,
            annotations=annotations,
        )

        # Test that annotations field cannot be reassigned (frozen behavior)
        with pytest.raises(Exception):
            tool.annotations = ToolAnnotations(title="New Annotations")

        # Test that annotations can be modified via model_copy
        new_annotations = ToolAnnotations(
            title="Modified Tool",
            readOnlyHint=False,
            destructiveHint=True,
        )
        modified_tool = tool.model_copy(update={"annotations": new_annotations})
        assert (
            modified_tool.annotations
            and modified_tool.annotations.title == "Modified Tool"
        )
        assert (
            tool.annotations and tool.annotations.title == "Test Tool"
        )  # Original unchanged
