"""Test tool JSON serialization with DiscriminatedUnionMixin."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from openhands.sdk.tool.builtins import FinishTool, ThinkTool
from openhands.sdk.tool.tool import ToolDefinition


def test_tool_serialization_deserialization() -> None:
    """Test that Tool supports polymorphic JSON serialization/deserialization."""
    # Use FinishTool which is a simple built-in tool
    tool = FinishTool.create()[0]

    # Serialize to JSON
    tool_json = tool.model_dump_json()

    # Deserialize from JSON using the abstract base class (for polymorphism)
    deserialized_tool = ToolDefinition.model_validate_json(tool_json)

    # Should deserialize to the correct type with same serializable data
    assert isinstance(deserialized_tool, FinishTool)
    assert tool.model_dump() == deserialized_tool.model_dump()


def test_tool_supports_polymorphic_field_json_serialization() -> None:
    """Test that Tool supports polymorphic JSON serialization when used as a field."""
    from typing import Any

    from openhands.sdk.tool.tool import get_polymorphic_tool_type

    # Use get_polymorphic_tool_type() for polymorphic deserialization
    PolymorphicTool: Any = get_polymorphic_tool_type()

    class Container(BaseModel):
        tool: PolymorphicTool  # type: ignore[valid-type]

    # Create container with tool
    tool = FinishTool.create()[0]
    container = Container(tool=tool)

    # Serialize to JSON
    container_json = container.model_dump_json()

    # Deserialize from JSON
    deserialized_container = Container.model_validate_json(container_json)

    # Should preserve the tool type with same serializable data
    assert isinstance(deserialized_container.tool, FinishTool)
    assert tool.model_dump() == deserialized_container.tool.model_dump()


def test_tool_supports_nested_polymorphic_json_serialization() -> None:
    """Test that Tool supports nested polymorphic JSON serialization."""
    from typing import Any

    from openhands.sdk.tool.tool import get_polymorphic_tool_type

    # Use get_polymorphic_tool_type() for polymorphic deserialization
    PolymorphicTool: Any = get_polymorphic_tool_type()

    class NestedContainer(BaseModel):
        tools: list[PolymorphicTool]  # type: ignore[valid-type]

    # Create container with multiple tools
    tool1 = FinishTool.create()[0]
    tool2 = ThinkTool.create()[0]
    container = NestedContainer(tools=[tool1, tool2])

    # Serialize to JSON
    container_json = container.model_dump_json()

    # Deserialize from JSON
    deserialized_container = NestedContainer.model_validate_json(container_json)

    # Should preserve all tool types with same serializable data
    assert len(deserialized_container.tools) == 2
    assert isinstance(deserialized_container.tools[0], FinishTool)
    assert isinstance(deserialized_container.tools[1], ThinkTool)
    assert tool1.model_dump() == deserialized_container.tools[0].model_dump()
    assert tool2.model_dump() == deserialized_container.tools[1].model_dump()


def test_tool_model_validate_json_dict() -> None:
    """Test that Tool.model_validate works with dict from JSON."""
    # Create tool
    tool = FinishTool.create()[0]

    # Serialize to JSON, then parse to dict
    tool_json = tool.model_dump_json()
    tool_dict = json.loads(tool_json)

    # Deserialize from dict using abstract base class (for polymorphism)
    deserialized_tool = ToolDefinition.model_validate(tool_dict)

    # Should have same serializable data
    assert isinstance(deserialized_tool, FinishTool)
    assert tool.model_dump() == deserialized_tool.model_dump()


def test_tool_no_fallback_behavior_json() -> None:
    """Test that Tool handles unknown types gracefully in JSON."""
    # Create JSON with unknown kind
    tool_dict = {
        "name": "test-tool",
        "description": "A test tool",
        "action_type": "FinishAction",
        "observation_type": None,
        "kind": "UnknownToolType",
    }
    tool_json = json.dumps(tool_dict)

    with pytest.raises(ValidationError):
        ToolDefinition.model_validate_json(tool_json)


def test_tool_type_annotation_works_json() -> None:
    """Test that ToolType annotation works correctly with JSON."""
    from typing import Any

    from openhands.sdk.tool.tool import get_polymorphic_tool_type

    # Create tool
    tool = FinishTool.create()[0]

    # Use get_polymorphic_tool_type() for polymorphic deserialization
    PolymorphicTool: Any = get_polymorphic_tool_type()

    class TestModel(BaseModel):
        tool: PolymorphicTool  # type: ignore[valid-type]

    model = TestModel(tool=tool)

    # Serialize to JSON
    model_json = model.model_dump_json()

    # Deserialize from JSON
    deserialized_model = TestModel.model_validate_json(model_json)

    # Should work correctly with same serializable data
    assert isinstance(deserialized_model.tool, FinishTool)
    assert tool.model_dump() == deserialized_model.tool.model_dump()


def test_tool_kind_field_json() -> None:
    """Test Tool kind field is correctly set and preserved through JSON."""
    # Create tool
    tool = FinishTool.create()[0]

    # Check kind field
    assert hasattr(tool, "kind")
    expected_kind = tool.__class__.__name__
    assert tool.kind == expected_kind

    # Serialize to JSON
    tool_json = tool.model_dump_json()

    # Deserialize from JSON using abstract base class (for polymorphism)
    deserialized_tool = ToolDefinition.model_validate_json(tool_json)

    # Should preserve kind field and correct type
    assert hasattr(deserialized_tool, "kind")
    assert deserialized_tool.kind == tool.kind
    assert isinstance(deserialized_tool, FinishTool)
