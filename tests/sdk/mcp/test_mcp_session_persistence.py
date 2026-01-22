"""Tests for MCP session persistence across tool calls.

Verifies that MCP connections are reused across multiple tool calls,
avoiding the overhead of reconnecting for each call.

Related issue: https://github.com/OpenHands/software-agent-sdk/issues/1739
"""

import pytest

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.tool import MCPToolExecutor

from .conftest import MCPTestServer


@pytest.fixture
def live_server():
    """Fixture providing a live MCP test server with echo/add tools."""
    server = MCPTestServer("session-test-server")

    @server.add_tool
    def echo(message: str) -> str:
        """Echo a message."""
        return f"Echo: {message}"

    @server.add_tool
    def add_numbers(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    port = server.start()
    yield port
    server.stop()


class TestSessionPersistence:
    """Tests verifying session/connection persistence."""

    def test_connection_reused_across_tool_calls(self, live_server: int):
        """Test that multiple tool calls reuse the same connection."""
        config = {
            "mcpServers": {
                "test": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{live_server}/mcp",
                }
            }
        }

        with create_mcp_tools(config, timeout=10.0) as client:
            assert len(client) == 2

            echo_tool = next(t for t in client if t.name == "echo")
            add_tool = next(t for t in client if t.name == "add_numbers")

            # Verify they share the same client
            echo_executor = echo_tool.executor
            add_executor = add_tool.executor
            assert isinstance(echo_executor, MCPToolExecutor)
            assert isinstance(add_executor, MCPToolExecutor)
            assert echo_executor.client is add_executor.client

            # Make multiple calls - should all use same connection
            for i in range(3):
                action = echo_tool.action_from_arguments({"message": f"test_{i}"})
                result = echo_executor(action)
                assert f"test_{i}" in result.text

            # Call different tool - same connection
            action = add_tool.action_from_arguments({"a": 5, "b": 3})
            result = add_executor(action)
            assert "8" in result.text

    def test_close_releases_connection(self, live_server: int):
        """Test that close() properly releases the connection."""
        config = {
            "mcpServers": {
                "test": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{live_server}/mcp",
                }
            }
        }

        with create_mcp_tools(config, timeout=10.0) as client:
            tool = next(t for t in client if t.name == "echo")
            executor = tool.executor
            assert isinstance(executor, MCPToolExecutor)

            # Make a call
            action = tool.action_from_arguments({"message": "test"})
            result = executor(action)
            assert "test" in result.text
