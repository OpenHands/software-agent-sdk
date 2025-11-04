"""Tests for MCP tool with security risk prediction."""

import mcp.types

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.mcp.tool import MCPToolDefinition


class MockMCPClient(MCPClient):
    """Mock MCPClient for testing that bypasses the complex constructor."""

    def __init__(self):
        # Skip the parent constructor to avoid needing transport
        pass

    def is_connected(self):
        return True

    async def call_tool_mcp(  # type: ignore[override]
        self, name: str, arguments: dict
    ):
        """Mock implementation that returns a successful result."""
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="Mock result")],
            isError=False,
        )

    def call_async_from_sync(self, coro_func, timeout=None, **kwargs):
        """Mock implementation for synchronous calling."""
        import asyncio

        async def wrapper():
            async with self:
                return await coro_func(**kwargs)

        return asyncio.run(wrapper())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def test_mcp_tool_to_openai_with_security_risk():
    """Test that MCP tool schema includes security_risk field correctly.

    This test reproduces the bug where MCP tools with security_risk enabled
    incorrectly include both 'data' and 'security_risk' fields in the schema
    instead of the actual tool parameters + security_risk.
    """
    # Create a fetch-like MCP tool
    mcp_tool_def = mcp.types.Tool(
        name="fetch_fetch",
        description="Fetch a URL",
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to fetch"}},
            "required": ["url"],
        },
    )

    mock_client = MockMCPClient()
    tools = MCPToolDefinition.create(mcp_tool=mcp_tool_def, mcp_client=mock_client)
    tool = tools[0]

    # Generate OpenAI tool schema WITH security risk prediction
    openai_tool = tool.to_openai_tool(add_security_risk_prediction=True)

    function_params = openai_tool["function"]["parameters"]  # type: ignore[typeddict-item]
    properties = function_params["properties"]
    required = function_params.get("required", [])

    # The schema should have 'url' and 'security_risk' fields
    # NOT 'data' and 'security_risk'
    props_list = list(properties.keys())
    assert "url" in properties, (
        f"Expected 'url' field in properties, but got: {props_list}"
    )
    assert "security_risk" in properties, (
        f"Expected 'security_risk' field in properties, but got: {props_list}"
    )

    # The schema should NOT have a 'data' field
    assert "data" not in properties, (
        f"Unexpected 'data' field in properties. Properties: {props_list}"
    )

    # Both fields should be required
    assert "url" in required, f"Expected 'url' in required, but got: {required}"
    assert "security_risk" in required, (
        f"Expected 'security_risk' in required, but got: {required}"
    )


def test_mcp_tool_action_from_arguments_with_security_risk():
    """Test that action_from_arguments works correctly with security_risk popped.

    This test simulates what happens in Agent._get_action_event where
    security_risk is popped from arguments before calling action_from_arguments.
    """
    # Create a fetch-like MCP tool
    mcp_tool_def = mcp.types.Tool(
        name="fetch_fetch",
        description="Fetch a URL",
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to fetch"}},
            "required": ["url"],
        },
    )

    mock_client = MockMCPClient()
    tools = MCPToolDefinition.create(mcp_tool=mcp_tool_def, mcp_client=mock_client)
    tool = tools[0]

    # Simulate LLM providing arguments with security_risk
    # (security_risk would be popped by Agent before calling action_from_arguments)
    arguments = {
        "url": "https://google.com",
        # security_risk has already been popped by Agent
    }

    # This should work and create an MCPToolAction with data field
    action = tool.action_from_arguments(arguments)

    assert isinstance(action, MCPToolAction)
    # Note: 'kind' field from DiscriminatedUnionMixin should NOT be in action.data
    # because it's not part of the MCP tool schema and would cause validation errors
    # when sent to the MCP server
    assert action.data == {"url": "https://google.com"}


def test_mcp_tool_validates_correctly_after_security_risk_pop():
    """Test that MCP tool validation works after security_risk is popped.

    This is the full integration test that reproduces the bug scenario:
    1. LLM generates arguments based on schema with security_risk
    2. Agent pops security_risk from arguments
    3. Agent calls tool.action_from_arguments with remaining arguments
    4. Tool should validate successfully (THIS IS WHERE THE BUG OCCURS)
    """
    # Create a fetch-like MCP tool
    mcp_tool_def = mcp.types.Tool(
        name="fetch_fetch",
        description="Fetch a URL",
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to fetch"}},
            "required": ["url"],
        },
    )

    mock_client = MockMCPClient()
    tools = MCPToolDefinition.create(mcp_tool=mcp_tool_def, mcp_client=mock_client)
    tool = tools[0]

    # Simulate what Agent does:
    # 1. Parse arguments from LLM
    llm_generated_arguments = {
        "url": "https://google.com",
        "security_risk": "LOW",
    }

    # 2. Pop security_risk (this is what Agent does in _get_action_event)
    llm_generated_arguments.pop("security_risk")

    # 3. Create action from remaining arguments
    # This should NOT fail with validation errors about 'data' field
    action = tool.action_from_arguments(llm_generated_arguments)

    # Verify the action is created correctly
    assert isinstance(action, MCPToolAction)
    # Note: 'kind' field from DiscriminatedUnionMixin should NOT be in action.data
    # because it's not part of the MCP tool schema and would cause validation errors
    # when sent to the MCP server
    assert action.data == {"url": "https://google.com"}

    # 4. Execute the action (this should also work)
    observation = tool(action)
    assert isinstance(observation, MCPToolObservation)
    assert not observation.is_error
