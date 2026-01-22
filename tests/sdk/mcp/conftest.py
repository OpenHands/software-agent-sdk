"""Shared fixtures and utilities for MCP tests."""

import asyncio
import socket
import threading
import time
from typing import Any, Literal

import mcp.types
import pytest
from fastmcp import FastMCP

from openhands.sdk.mcp.client import MCPClient


class MockMCPClient(MCPClient):
    """Mock MCPClient for testing that bypasses the complex constructor.

    This mock provides minimal functionality needed for unit tests without
    requiring actual network connections or MCP server infrastructure.
    """

    def __init__(self):
        # Skip the parent constructor to avoid needing transport
        self._session_id = None
        self._server_url = None
        self._connection_count = 0
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    @property
    def session_id(self):
        return self._session_id

    @property
    def server_url(self):
        return self._server_url

    async def call_tool_mcp(  # type: ignore[override]
        self, name: str, arguments: dict[str, Any]
    ) -> mcp.types.CallToolResult:
        """Mock implementation that returns a successful result."""
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="Mock result")],
            isError=False,
        )

    def call_async_from_sync(
        self, coro_func, timeout: float | None = None, **kwargs
    ) -> Any:
        """Mock implementation for synchronous calling."""

        async def wrapper():
            return await coro_func(**kwargs)

        return asyncio.run(wrapper())

    async def __aenter__(self):
        self._connection_count += 1
        return self

    async def __aexit__(self, *args):
        self._connection_count -= 1


def _find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MCPTestServer:
    """Reusable MCP test server for integration tests.

    Can be configured with custom tools via setup_tools callback.
    Supports per-session state tracking for testing session persistence.
    """

    def __init__(self, name: str = "test-server"):
        self.mcp = FastMCP(name)
        self.port: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sessions: dict[str, dict] = {}  # session_id -> state

    def add_tool(self, func):
        """Add a tool to the server."""
        return self.mcp.tool()(func)

    @property
    def sessions(self) -> dict[str, dict]:
        """Access to session state for testing."""
        return self._sessions

    def clear_sessions(self):
        """Clear all session state."""
        self._sessions.clear()

    def start(
        self,
        transport: Literal["http", "streamable-http", "sse"] = "http",
        path: str = "/mcp",
    ) -> int:
        """Start the server and return the port."""
        self.port = _find_free_port()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(
                self.mcp.run_http_async(
                    host="127.0.0.1",
                    port=self.port,
                    transport=transport,
                    show_banner=False,
                    path=path,
                )
            )

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        time.sleep(0.5)  # Wait for server to start
        return self.port

    def stop(self):
        """Stop the server.

        Note: This may produce RuntimeError warnings in the daemon thread
        because the event loop is stopped before the server future completes.
        This is expected behavior for test cleanup.
        """
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread = None
        self._loop = None
        self.port = None


@pytest.fixture
def mock_mcp_client() -> MockMCPClient:
    """Fixture providing a mock MCP client for unit tests."""
    return MockMCPClient()
