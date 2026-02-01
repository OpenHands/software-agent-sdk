import pytest
from pydantic import ValidationError

from openhands.sdk.mcp import MCPToolAction


class _ChildMCPToolActionForSerialization(MCPToolAction):
    """Child MCP action for testing declared fields with data.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

    declared: int


def test_data_field_emerges_from_to_mcp_arguments():
    """Test that data field contents are returned by to_mcp_arguments."""
    data = {"new_field": "value", "dynamic": 123}
    a = MCPToolAction(data=data)
    out = a.to_mcp_arguments()

    # Data field contents should be returned
    assert out["new_field"] == "value"
    assert out["dynamic"] == 123
    assert out == data


def test_declared_child_fields_with_data():
    """Test that child classes work with the data field."""
    data = {"tool_param": "value"}
    a = _ChildMCPToolActionForSerialization(declared=7, data=data)
    out = a.to_mcp_arguments()

    # Only data field contents should be in MCP arguments
    assert out == {"tool_param": "value"}
    # The declared field should be accessible but not in MCP arguments
    assert a.declared == 7


def test_empty_data_field():
    """Test behavior with empty data field."""
    a = MCPToolAction()
    out = a.to_mcp_arguments()
    assert out == {}


def test_data_field_with_none_values():
    """Test that None values in data are preserved."""
    data = {"keep_me": "ok", "drop_me": None}
    a = MCPToolAction(data=data)
    out = a.to_mcp_arguments()
    assert out.get("keep_me") == "ok"
    assert out.get("drop_me") is None  # None values are preserved in data


def test_frozen_model_is_immutable():
    """Test that MCPToolAction is immutable."""
    a = MCPToolAction(data={"x": 1})
    with pytest.raises(ValidationError):
        a.data = {"y": 2}  # type: ignore


def test_data_field_type_validation():
    """Test that data field accepts dict[str, Any]."""
    # Valid data
    a = MCPToolAction(data={"string": "value", "number": 123, "bool": True})
    assert a.data == {"string": "value", "number": 123, "bool": True}

    # Empty dict is valid
    b = MCPToolAction(data={})
    assert b.data == {}


def test_extra_fields_not_allowed():
    """Test that extra fields are not allowed outside of data."""
    with pytest.raises(ValidationError):
        MCPToolAction(extra_field="not_allowed")  # type: ignore
