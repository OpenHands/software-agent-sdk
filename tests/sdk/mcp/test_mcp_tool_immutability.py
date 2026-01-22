"""Tests for MCP tool functionality with new simplified implementation."""

from typing import cast
from unittest.mock import MagicMock, Mock

import mcp.types
import pytest

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition, MCPToolExecutor


class MockMCPClient(MCPClient):
    """Mock MCPClient for testing that bypasses the complex constructor."""

    def __init__(self):
        # Skip the parent constructor to avoid needing transport
        pass


class TestMCPToolImmutability:
    """Test suite for MCPTool immutability features."""

    def setup_method(self):
        """Set up test environment."""
        self.mock_client: MockMCPClient = MockMCPClient()

        # Create a mock MCP tool
        self.mock_mcp_tool: Mock = MagicMock(spec=mcp.types.Tool)
        self.mock_mcp_tool.name = "test_tool"
        self.mock_mcp_tool.description = "Test tool description"
        self.mock_mcp_tool.inputSchema = {
            "type": "object",
            "properties": {"command": {"type": "string"}},
        }
        self.mock_mcp_tool.annotations = None
        self.mock_mcp_tool.meta = {"version": "1.0"}

        tools = MCPToolDefinition.create(
            mcp_tool=self.mock_mcp_tool, mcp_client=self.mock_client
        )
        self.tool: MCPToolDefinition = tools[0]  # Extract single tool from sequence

    def test_mcp_tool_is_frozen(self):
        """Test that MCPTool instances are frozen and cannot be modified."""
        # Test that direct field assignment raises ValidationError
        with pytest.raises(
            Exception
        ):  # Pydantic raises ValidationError for frozen models
            self.tool.mcp_tool = mcp.types.Tool(
                name="modified_name",
                description="modified description",
                inputSchema={"type": "object", "properties": {}},
            )

        with pytest.raises(Exception):
            self.tool.description = "modified_description"

    def test_mcp_tool_set_executor_returns_new_instance(self):
        """Test that set_executor returns a new MCPTool instance."""
        new_executor = MCPToolExecutor(tool_name="new_tool", client=self.mock_client)
        new_tool = self.tool.set_executor(new_executor)

        # Verify that a new instance was created
        assert new_tool is not self.tool
        assert cast(MCPToolExecutor, self.tool.executor).tool_name == "test_tool"
        assert cast(MCPToolExecutor, new_tool.executor).tool_name == "new_tool"
        assert new_tool.name == self.tool.name
        assert new_tool.description == self.tool.description

    def test_mcp_tool_model_copy_creates_modified_instance(self):
        """Test that model_copy can create modified versions of MCPTool instances."""
        # Create a modified MCP tool with a different name
        from mcp.types import Tool as MCPTool

        modified_mcp_tool = MCPTool(
            name="modified_tool",
            description="Modified MCP tool description",
            inputSchema=self.tool.mcp_tool.inputSchema,
        )

        # Create a copy with modified fields
        modified_tool = self.tool.model_copy(
            update={
                "mcp_tool": modified_mcp_tool,
                "description": "Modified description",
            }
        )

        # Verify that a new instance was created with modifications
        assert modified_tool is not self.tool
        assert self.tool.name == "test_tool"
        assert self.tool.description == "Test tool description"
        assert modified_tool.name == "modified_tool"
        assert modified_tool.description == "Modified description"

    def test_mcp_tool_meta_field_immutability(self):
        """Test that the meta field works correctly and is immutable."""
        # Verify meta field is accessible
        assert self.tool.meta == {"version": "1.0"}

        # Test that meta field cannot be directly modified
        with pytest.raises(Exception):
            self.tool.meta = {"version": "2.0"}

        # Test that meta field can be modified via model_copy
        new_meta = {"version": "2.0", "author": "new_author"}
        modified_tool = self.tool.model_copy(update={"meta": new_meta})
        assert modified_tool.meta == new_meta
        assert self.tool.meta == {"version": "1.0"}  # Original unchanged

    def test_mcp_tool_extra_fields_immutability(self):
        """Test that MCPTool extra fields (mcp_client, mcp_tool) are immutable."""

        with pytest.raises(Exception):
            self.tool.mcp_tool = self.mock_mcp_tool

        assert self.tool.mcp_tool is self.mock_mcp_tool

    def test_mcp_tool_create_immutable_instance(self):
        """Test that MCPToolDefinition.create() creates immutable instances."""
        # Create another tool using create
        mock_tool2 = MagicMock(spec=mcp.types.Tool)
        mock_tool2.name = "another_tool"
        mock_tool2.description = "Another test tool"
        mock_tool2.inputSchema = {"type": "object"}
        mock_tool2.annotations = None
        mock_tool2.meta = None

        tools2 = MCPToolDefinition.create(
            mcp_tool=mock_tool2, mcp_client=self.mock_client
        )
        tool2 = tools2[0]  # Extract single tool from sequence

        # Verify it's immutable
        with pytest.raises(Exception):
            tool2.mcp_tool = mcp.types.Tool(
                name="modified_name",
                description="modified description",
                inputSchema={"type": "object", "properties": {}},
            )

        # Verify it has the correct properties
        assert tool2.name == "another_tool"
        assert tool2.description == "Another test tool"
        assert isinstance(tool2.executor, MCPToolExecutor)
