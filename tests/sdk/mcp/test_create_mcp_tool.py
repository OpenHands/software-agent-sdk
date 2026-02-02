"""Tests for MCP utils functionality - integration tests with real MCP servers."""

import asyncio
import logging
import socket
import threading
import time
from collections.abc import Generator
from typing import Literal
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastmcp import FastMCP

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.exceptions import MCPTimeoutError


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
            # Try HTTP request since MCP servers use HTTP
            with httpx.Client(timeout=interval) as client:
                client.get(f"http://127.0.0.1:{port}/")
                return
        except httpx.ConnectError:
            pass
        except (httpx.TimeoutException, httpx.HTTPStatusError):
            # Any response (even errors) means server is up
            return
        except Exception:
            # Any other response means server is up
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

        # Wait for server to be ready by polling the port
        _wait_for_port(self.port)

        # Check if server thread failed during startup
        if startup_error:
            raise startup_error[0]

        return self.port

    def stop(self):
        """Stop the server and clean up resources."""
        if self._server_thread is not None:
            # Daemon thread will clean up automatically when process exits
            self._server_thread = None
        self.port = None


@pytest.fixture
def http_mcp_server() -> Generator[MCPTestServer, None, None]:
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
def sse_mcp_server() -> Generator[MCPTestServer, None, None]:
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


def test_create_mcp_tools_empty_config():
    """Test creating MCP tools with empty configuration raises error."""
    config = {}
    with pytest.raises(ValueError, match="No MCP servers defined"):
        create_mcp_tools(config)


def test_create_mcp_tools_http_server(http_mcp_server: MCPTestServer):
    """Test creating MCP tools with a real HTTP server."""
    config = {
        "mcpServers": {
            "http_server": {
                "transport": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)

    assert len(tools) == 2
    tool_names = {t.name for t in tools}
    assert "greet" in tool_names
    assert "add_numbers" in tool_names

    # Verify tool schemas are properly loaded
    greet_tool = next(t for t in tools if t.name == "greet")
    openai_schema = greet_tool.to_openai_tool()
    assert openai_schema["type"] == "function"
    assert "parameters" in openai_schema["function"]
    assert "name" in openai_schema["function"]["parameters"]["properties"]


def test_create_mcp_tools_sse_server(sse_mcp_server: MCPTestServer):
    """Test creating MCP tools with a real SSE server."""
    config = {
        "mcpServers": {
            "sse_server": {
                "transport": "sse",
                "url": f"http://127.0.0.1:{sse_mcp_server.port}/sse",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)

    assert len(tools) == 2
    tool_names = {t.name for t in tools}
    assert "echo" in tool_names
    assert "multiply" in tool_names


def test_create_mcp_tools_mixed_servers(
    http_mcp_server: MCPTestServer, sse_mcp_server: MCPTestServer
):
    """Test creating MCP tools with both HTTP and SSE servers."""
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

    tools = create_mcp_tools(config, timeout=10.0)

    # Should have tools from both servers (prefixed with server name)
    assert len(tools) == 4
    tool_names = {t.name for t in tools}
    assert "http_server_greet" in tool_names
    assert "http_server_add_numbers" in tool_names
    assert "sse_server_echo" in tool_names
    assert "sse_server_multiply" in tool_names


def test_create_mcp_tools_http_schema_validation(http_mcp_server: MCPTestServer):
    """Test that tool schemas are properly loaded from HTTP server."""
    config = {
        "mcpServers": {
            "http_server": {
                "transport": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)
    add_tool = next(t for t in tools if t.name == "add_numbers")

    openai_schema = add_tool.to_openai_tool()
    params = openai_schema["function"].get("parameters", {})
    assert params["properties"]["a"]["type"] == "integer"
    assert params["properties"]["b"]["type"] == "integer"
    assert "a" in params["required"]
    assert "b" in params["required"]


def test_create_mcp_tools_transport_inferred_from_url(http_mcp_server: MCPTestServer):
    """Test that transport type is inferred when not explicitly specified."""
    config = {
        "mcpServers": {
            "auto_http": {
                # No explicit transport - should infer from URL
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)
    assert len(tools) == 2


def test_create_mcp_tools_sse_inferred_from_url(sse_mcp_server: MCPTestServer):
    """Test that SSE transport is inferred from URL containing /sse."""
    config = {
        "mcpServers": {
            "auto_sse": {
                # No explicit transport - should infer SSE from /sse in URL
                "url": f"http://127.0.0.1:{sse_mcp_server.port}/sse",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)
    assert len(tools) == 2


def test_execute_http_tool(http_mcp_server: MCPTestServer):
    """Test executing a tool on an HTTP MCP server."""
    config = {
        "mcpServers": {
            "http_server": {
                "transport": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)
    greet_tool = next(t for t in tools if t.name == "greet")

    action = greet_tool.action_from_arguments({"name": "World"})
    assert greet_tool.executor is not None
    observation = greet_tool.executor(action)

    assert observation is not None
    assert "Hello, World!" in observation.text


def test_execute_sse_tool(sse_mcp_server: MCPTestServer):
    """Test executing a tool on an SSE MCP server."""
    config = {
        "mcpServers": {
            "sse_server": {
                "transport": "sse",
                "url": f"http://127.0.0.1:{sse_mcp_server.port}/sse",
            }
        }
    }

    tools = create_mcp_tools(config, timeout=10.0)
    multiply_tool = next(t for t in tools if t.name == "multiply")

    action = multiply_tool.action_from_arguments({"x": 6, "y": 7})
    assert multiply_tool.executor is not None
    observation = multiply_tool.executor(action)

    assert observation is not None
    assert "42" in observation.text


def test_create_mcp_tools_connection_to_nonexistent_server():
    """Test that connection to non-existent server fails gracefully."""
    config = {
        "mcpServers": {
            "nonexistent": {
                "transport": "http",
                "url": "http://127.0.0.1:59999/mcp",
            }
        }
    }

    # Should either return empty tools or raise connection-related errors
    # Key is it shouldn't hang
    try:
        tools = create_mcp_tools(config, timeout=5.0)
        assert len(tools) == 0  # No tools from failed connection
    except (ConnectionError, TimeoutError, MCPTimeoutError, OSError, RuntimeError):
        pass  # Expected connection errors are acceptable


def test_create_mcp_tools_stdio_server():
    """Test creating MCP tools with dict configuration (not MCPConfig object)."""
    mcp_config = {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }

    # Use longer timeout for CI environments where uvx may need to download packages
    tools = create_mcp_tools(mcp_config, timeout=120.0)
    assert len(tools) == 1
    assert tools[0].name == "fetch"

    # Get the schema from the OpenAI tool since MCPToolAction now uses dynamic
    # schema
    openai_tool = tools[0].to_openai_tool()
    assert openai_tool["type"] == "function"
    assert "parameters" in openai_tool["function"]
    input_schema = openai_tool["function"]["parameters"]

    assert "type" in input_schema
    assert input_schema["type"] == "object"
    assert "properties" in input_schema
    assert "url" in input_schema["properties"]
    assert input_schema["properties"]["url"]["type"] == "string"
    assert "required" in input_schema
    assert "url" in input_schema["required"]

    # security_risk should NOT be in the schema when no security analyzer is enabled
    assert "security_risk" not in input_schema["required"]
    assert "security_risk" not in input_schema["properties"]

    mcp_tool = tools[0].to_mcp_tool()
    mcp_schema = mcp_tool["inputSchema"]

    # Check that both schemas have the same essential structure
    assert mcp_schema["type"] == input_schema["type"]
    assert set(mcp_schema["required"]) == set(input_schema["required"])

    # Check that all properties from input_schema exist in mcp_schema
    # (excluding meta fields like 'summary' which are for LLM, not tool interface)
    for prop_name, prop_def in input_schema["properties"].items():
        if prop_name == "summary":
            continue  # summary is a meta field for LLM, not part of tool interface
        assert prop_name in mcp_schema["properties"]
        assert mcp_schema["properties"][prop_name]["type"] == prop_def["type"]
        assert (
            mcp_schema["properties"][prop_name]["description"]
            == prop_def["description"]
        )

    assert openai_tool["function"]["name"] == "fetch"

    # security_risk should NOT be in the OpenAI tool schema when no security analyzer is enabled  # noqa: E501
    assert "security_risk" not in input_schema["required"]
    assert "security_risk" not in input_schema["properties"]

    assert tools[0].executor is not None


def test_create_mcp_tools_timeout_error_message():
    """Test that timeout errors are wrapped with informative error messages.

    Note: This test uses mocking to simulate a timeout since waiting for real
    timeouts would be slow and flaky.
    """
    config = {
        "mcpServers": {
            "slow_server": {
                "transport": "stdio",
                "command": "python",
                "args": ["./slow_server.py"],
            },
            "another_server": {
                "transport": "http",
                "url": "https://api.example.com/mcp",
            },
        }
    }

    with patch("openhands.sdk.mcp.utils.MCPClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.call_async_from_sync.side_effect = TimeoutError()

        with pytest.raises(MCPTimeoutError) as exc_info:
            create_mcp_tools(config, timeout=30.0)

        error_message = str(exc_info.value)
        assert "30" in error_message
        assert "seconds" in error_message
        assert "slow_server" in error_message
        assert "another_server" in error_message
        assert "Possible solutions" in error_message
        assert "timeout" in error_message.lower()

        assert exc_info.value.timeout == 30.0
        assert exc_info.value.config is not None
