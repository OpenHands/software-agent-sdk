"""Tests for MCP graceful degradation functionality."""

import asyncio
import logging
import socket
import threading
import time
from collections.abc import Generator
from typing import Literal

import httpx
import pytest
from fastmcp import FastMCP

from openhands.sdk.mcp import (
    MCPServerError,
    MCPToolsResult,
    create_mcp_tools_graceful,
)


logger = logging.getLogger(__name__)

MCPTransport = Literal["http", "streamable-http", "sse"]


def _find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0, interval: float = 0.1) -> None:
    """Wait for a port to become available by polling with HTTP requests."""
    max_attempts = int(timeout / interval)
    for _ in range(max_attempts):
        try:
            with httpx.Client(timeout=interval) as client:
                client.get(f"http://127.0.0.1:{port}/")
                return
        except httpx.ConnectError:
            pass
        except (httpx.TimeoutException, httpx.HTTPStatusError):
            return
        except Exception:
            return
        time.sleep(interval)
    raise RuntimeError(f"Server failed to start on port {port} within {timeout}s")


class MCPTestServer:
    """Helper class to manage MCP test servers for testing."""

    def __init__(self, name: str = "test-server"):
        self.mcp = FastMCP(name)
        self.port: int | None = None
        self._server_thread: threading.Thread | None = None

    def add_tool(self, func):
        """Add a tool to the server."""
        return self.mcp.tool()(func)

    def start(self, transport: MCPTransport = "http") -> int:
        """Start the server and return the port."""
        self.port = _find_free_port()
        path = "/sse" if transport == "sse" else "/mcp"
        startup_error: list[Exception] = []

        async def run_server():
            assert self.port is not None
            await self.mcp.run_http_async(
                host="127.0.0.1",
                port=self.port,
                transport=transport,
                show_banner=False,
                path=path,
            )

        def server_thread_target():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_server())
            except Exception as e:
                logger.error(f"MCP test server failed: {e}")
                startup_error.append(e)
            finally:
                loop.close()

        self._server_thread = threading.Thread(target=server_thread_target, daemon=True)
        self._server_thread.start()
        _wait_for_port(self.port)

        if startup_error:
            raise startup_error[0]

        return self.port

    def stop(self):
        """Stop the server and clean up resources."""
        if self._server_thread is not None:
            self._server_thread = None
        self.port = None


@pytest.fixture
def http_mcp_server() -> Generator[MCPTestServer]:
    """Fixture providing a running HTTP MCP server with test tools."""
    server = MCPTestServer("http-test-server")

    @server.add_tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @server.add_tool
    def add_numbers(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    server.start(transport="http")
    yield server
    server.stop()


@pytest.fixture
def sse_mcp_server() -> Generator[MCPTestServer]:
    """Fixture providing a running SSE MCP server with test tools."""
    server = MCPTestServer("sse-test-server")

    @server.add_tool
    def echo(message: str) -> str:
        """Echo a message back."""
        return message

    @server.add_tool
    def multiply(x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    server.start(transport="sse")
    yield server
    server.stop()


def test_create_mcp_tools_graceful_empty_config():
    """Test graceful creation with empty config returns empty result."""
    result = create_mcp_tools_graceful({})
    assert isinstance(result, MCPToolsResult)
    assert len(result.tools) == 0
    assert len(result.errors) == 0
    assert not result.has_errors


def test_create_mcp_tools_graceful_single_server(http_mcp_server: MCPTestServer):
    """Test graceful creation with a single working server."""
    config = {
        "mcpServers": {
            "http_server": {
                "transport": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            }
        }
    }

    result = create_mcp_tools_graceful(config, timeout=10.0)

    assert len(result.tools) == 2
    assert not result.has_errors
    tool_names = {t.name for t in result.tools}
    assert "greet" in tool_names
    assert "add_numbers" in tool_names


def test_create_mcp_tools_graceful_with_failing_server():
    """Test graceful creation when one server fails."""
    config = {
        "mcpServers": {
            "nonexistent": {
                "transport": "http",
                "url": "http://127.0.0.1:59999/mcp",
            }
        }
    }

    result = create_mcp_tools_graceful(config, timeout=5.0)

    assert len(result.tools) == 0
    assert result.has_errors
    assert len(result.errors) == 1
    assert result.errors[0].server_name == "nonexistent"


def test_create_mcp_tools_graceful_mixed_success_failure(
    http_mcp_server: MCPTestServer,
):
    """Test graceful degradation with one working and one failing server."""
    config = {
        "mcpServers": {
            "working_server": {
                "transport": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            },
            "failing_server": {
                "transport": "http",
                "url": "http://127.0.0.1:59999/mcp",
            },
        }
    }

    result = create_mcp_tools_graceful(config, timeout=5.0)

    # Should have tools from working server
    # Note: since each server is initialized independently, tools are NOT prefixed
    assert len(result.tools) == 2
    tool_names = {t.name for t in result.tools}
    assert "greet" in tool_names
    assert "add_numbers" in tool_names

    # Should have error for failing server
    assert result.has_errors
    assert len(result.errors) == 1
    assert result.errors[0].server_name == "failing_server"


def test_create_mcp_tools_graceful_multiple_working_servers(
    http_mcp_server: MCPTestServer, sse_mcp_server: MCPTestServer
):
    """Test graceful creation with multiple working servers."""
    config = {
        "mcpServers": {
            "http_server": {
                "transport": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            },
            "sse_server": {
                "transport": "sse",
                "url": f"http://127.0.0.1:{sse_mcp_server.port}/sse",
            },
        }
    }

    result = create_mcp_tools_graceful(config, timeout=10.0)

    # Should have tools from both servers
    # Note: since each server is initialized independently (single-server config),
    # tools are NOT prefixed with server names
    assert len(result.tools) == 4
    assert not result.has_errors
    tool_names = {t.name for t in result.tools}
    assert "greet" in tool_names
    assert "add_numbers" in tool_names
    assert "echo" in tool_names
    assert "multiply" in tool_names


def test_create_mcp_tools_graceful_all_servers_fail():
    """Test graceful degradation when all servers fail."""
    config = {
        "mcpServers": {
            "server1": {
                "transport": "http",
                "url": "http://127.0.0.1:59999/mcp",
            },
            "server2": {
                "transport": "http",
                "url": "http://127.0.0.1:59998/mcp",
            },
        }
    }

    result = create_mcp_tools_graceful(config, timeout=5.0)

    # Should have no tools
    assert len(result.tools) == 0

    # Should have errors for both servers
    assert result.has_errors
    assert len(result.errors) == 2
    error_names = {e.server_name for e in result.errors}
    assert "server1" in error_names
    assert "server2" in error_names


def test_mcp_tools_result_error_summary():
    """Test that error_summary formats errors correctly."""
    result = MCPToolsResult(
        tools=[],
        errors=[
            MCPServerError("Connection refused", "server1", None),
            MCPServerError("Timeout", "server2", None),
        ],
    )

    summary = result.error_summary()
    assert "MCP server initialization failures:" in summary
    assert "server1" in summary
    assert "server2" in summary


def test_mcp_tools_result_error_summary_empty():
    """Test that error_summary returns empty string when no errors."""
    result = MCPToolsResult(tools=[], errors=[])
    assert result.error_summary() == ""


def test_mcp_server_error_attributes():
    """Test MCPServerError exception attributes."""
    cause = ValueError("test error")
    error = MCPServerError(
        message="Server failed",
        server_name="test_server",
        cause=cause,
    )

    assert error.server_name == "test_server"
    assert error.cause is cause
    assert str(error) == "Server failed"
