"""Tests for MCP session persistence across tool calls.

Verifies that MCP connections are reused across multiple tool calls,
avoiding the overhead of reconnecting for each call.

Related issue: https://github.com/OpenHands/software-agent-sdk/issues/1739
"""

import asyncio
import socket
import threading
import time

import pytest
from fastmcp import FastMCP

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.tool import MCPToolExecutor


def _find_free_port() -> int:
    """Find an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveMCPTestServer:
    """Test MCP server that tracks session IDs."""

    def __init__(self):
        self.mcp = FastMCP("test-server")
        self.port: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._setup_tools()

    def _setup_tools(self):
        @self.mcp.tool()
        def echo(message: str) -> str:
            """Echo a message."""
            return f"Echo: {message}"

        @self.mcp.tool()
        def add_numbers(a: int, b: int) -> str:
            """Add two numbers."""
            return str(a + b)

    def start(self) -> int:
        """Start the server, return the port."""
        self.port = _find_free_port()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(
                self.mcp.run_http_async(
                    host="127.0.0.1",
                    port=self.port,
                    transport="http",
                    show_banner=False,
                    path="/mcp",
                )
            )

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        time.sleep(0.5)  # Wait for server to start
        return self.port

    def stop(self):
        """Stop the server."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


@pytest.fixture
def live_server():
    """Fixture providing a live MCP test server."""
    server = LiveMCPTestServer()
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

        with create_mcp_tools(config, timeout=10.0) as toolset:
            assert len(toolset) == 2

            echo_tool = next(t for t in toolset if t.name == "echo")
            add_tool = next(t for t in toolset if t.name == "add_numbers")

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

        with create_mcp_tools(config, timeout=10.0) as toolset:
            tool = next(t for t in toolset if t.name == "echo")
            executor = tool.executor
            assert isinstance(executor, MCPToolExecutor)

            # Make a call
            action = tool.action_from_arguments({"message": "test"})
            result = executor(action)
            assert "test" in result.text

        # Connection is automatically closed when exiting context manager
