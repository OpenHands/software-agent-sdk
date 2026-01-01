"""Test MCP tool JSON serialization with DiscriminatedUnionMixin.

Note: MCPTool serialization may be limited due to complex MCP objects
(mcp_tool field contains mcp.types.Tool which may not be fully JSON serializable).
These tests demonstrate the expected behavior and limitations.
"""

from unittest.mock import Mock

import mcp.types

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import ToolDefinition


def create_mock_mcp_tool(name: str) -> mcp.types.Tool:
    """Create a mock MCP tool for testing."""
    return mcp.types.Tool(
        name=name,
        description=f"A test MCP tool named {name}",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query parameter"}
            },
            "required": ["query"],
        },
    )


def test_mcp_tool_json_serialization_deserialization() -> None:
    # Create mock MCP tool and client
    mock_mcp_tool = create_mock_mcp_tool(
        "test_mcp_tool_json_serialization_deserialization"
    )
    mock_client = Mock(spec=MCPClient)
    tools = MCPToolDefinition.create(mock_mcp_tool, mock_client)
    mcp_tool = tools[0]  # Extract single tool from sequence

    tool_json = mcp_tool.model_dump_json()
    deserialized_tool = MCPToolDefinition.model_validate_json(tool_json)
    assert isinstance(deserialized_tool, MCPToolDefinition)
    # We use model_dump because tool executor is not serializable and is excluded
    assert deserialized_tool.model_dump() == mcp_tool.model_dump()


def test_mcp_tool_polymorphic_behavior() -> None:
    """Test MCPTool polymorphic behavior using Tool base class."""
    # Create mock MCP tool and client
    mock_mcp_tool = create_mock_mcp_tool("test_mcp_tool_polymorphic_behavior")
    mock_client = Mock(spec=MCPClient)

    # Create MCPTool instance
    tools = MCPToolDefinition.create(mock_mcp_tool, mock_client)
    mcp_tool = tools[0]  # Extract single tool from sequence

    # Should be instance of ToolDefinition
    assert isinstance(mcp_tool, ToolDefinition)
    assert isinstance(mcp_tool, MCPToolDefinition)

    # Check basic properties
    assert mcp_tool.name == "test_mcp_tool_polymorphic_behavior"
    assert "test MCP tool" in mcp_tool.description
    assert hasattr(mcp_tool, "mcp_tool")


def test_mcp_tool_kind_field() -> None:
    """Test that MCPTool kind field is correctly set."""
    # Create mock MCP tool and client
    mock_mcp_tool = create_mock_mcp_tool("test_mcp_tool_kind_field")
    mock_client = Mock(spec=MCPClient)

    # Create MCPTool instance
    tools = MCPToolDefinition.create(mock_mcp_tool, mock_client)
    mcp_tool = tools[0]  # Extract single tool from sequence

    # Check kind field
    assert hasattr(mcp_tool, "kind")
    expected_kind = mcp_tool.__class__.__name__
    assert mcp_tool.kind == expected_kind


def test_mcp_tool_fallback_behavior() -> None:
    """Test MCPTool fallback behavior with manual data."""
    # Create data that could represent an MCPTool
    tool_data = {
        "name": "fallback-tool",
        "description": "A fallback test tool",
        "action_type": "MCPToolAction",
        "observation_type": "MCPToolObservation",
        "kind": "MCPToolDefinition",
        "mcp_tool": {
            "name": "fallback-tool",
            "description": "A fallback test tool",
            "inputSchema": {"type": "object", "properties": {}},
        },
    }

    deserialized_tool = ToolDefinition.model_validate(tool_data)
    assert isinstance(deserialized_tool, ToolDefinition)
    assert deserialized_tool.name == "fallback-tool"
    assert issubclass(deserialized_tool.action_type, Action)
    assert deserialized_tool.observation_type and issubclass(
        deserialized_tool.observation_type, MCPToolObservation
    )


def test_mcp_tool_essential_properties() -> None:
    """Test that MCPTool maintains essential properties after creation."""
    # Create mock MCP tool with specific properties
    mock_mcp_tool = mcp.types.Tool(
        name="essential_tool",
        description="Tool with essential properties",
        inputSchema={
            "type": "object",
            "properties": {"param1": {"type": "string"}, "param2": {"type": "integer"}},
            "required": ["param1"],
        },
    )
    mock_client = Mock(spec=MCPClient)

    # Create MCPTool instance
    tools = MCPToolDefinition.create(mock_mcp_tool, mock_client)
    mcp_tool = tools[0]  # Extract single tool from sequence

    # Verify essential properties are preserved
    assert mcp_tool.name == "essential_tool"
    assert mcp_tool.description == "Tool with essential properties"
    assert mcp_tool.mcp_tool.name == "essential_tool"
    assert mcp_tool.mcp_tool.inputSchema is not None

    # Verify action type was created correctly
    assert mcp_tool.action_type is not None and issubclass(
        mcp_tool.action_type, MCPToolAction
    )
    assert hasattr(mcp_tool.action_type, "to_mcp_arguments")
