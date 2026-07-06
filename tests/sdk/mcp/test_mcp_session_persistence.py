"""Tests for MCP session persistence across tool calls.

Verifies that MCP connections are reused across multiple tool calls,
avoiding the overhead of reconnecting for each call.

Also covers recovery from the fastmcp nesting-counter stuck state: when a
background session task dies while nesting_counter > 0, MCPClient.connect()
must auto-reset and reconnect rather than raising an internal RuntimeError.

Related issue: https://github.com/OpenHands/software-agent-sdk/issues/1739
"""

import asyncio
import socket
import threading
import time

import pytest
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolExecutor


def _find_free_port() -> int:
    """Find an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server():
    """Fixture providing a live MCP test server with echo/add tools."""
    mcp = FastMCP("session-test-server")

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo a message."""
        return f"Echo: {message}"

    @mcp.tool()
    def add_numbers(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    port = _find_free_port()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            mcp.run_http_async(
                host="127.0.0.1",
                port=port,
                transport="http",
                show_banner=False,
                path="/mcp",
            )
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.5)
    yield port


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


class TestNestingCounterRecovery:
    """Regression tests for the fastmcp nesting-counter stuck state.

    fastmcp tracks re-entrant context-manager use with an integer
    nesting_counter.  When a background session task dies unexpectedly
    (server 500, connection drop, task cancellation) while the counter is
    non-zero, fastmcp does NOT reset the counter.  Any subsequent attempt to
    start a new session raises:

        RuntimeError: Internal error: nesting counter should be 0
                      when starting new session, got N

    MCPClient.connect() works around this by detecting the stuck state and
    force-closing before reconnecting.
    """

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    async def _kill_session(client: MCPClient) -> None:
        """Cancel the background session task, mimicking a server-side failure."""
        task = client._session_state.session_task
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    @staticmethod
    def _counter(client: MCPClient) -> int:
        return client._session_state.nesting_counter

    @staticmethod
    def _task_done(client: MCPClient) -> bool:
        task = client._session_state.session_task
        return task is None or task.done()

    # ── tests ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_connect_recovers_from_stuck_counter_at_1(
        self, live_server: int
    ) -> None:
        """connect() after session death (counter=1) succeeds instead of raising."""
        url = f"http://127.0.0.1:{live_server}/mcp"
        client = MCPClient(StreamableHttpTransport(url))

        await client.connect()
        assert self._counter(client) == 1

        await self._kill_session(client)
        assert self._task_done(client)
        assert self._counter(client) == 1  # stuck – the bug

        # With the fix, connect() auto-recovers: no exception raised.
        await client.connect()
        assert self._counter(client) == 1
        assert not self._task_done(client)

        await client.close()

    @pytest.mark.asyncio
    async def test_connect_recovers_from_stuck_counter_at_3(
        self, live_server: int
    ) -> None:
        """connect() after session death with counter=3 matches the 'got 3' error."""
        url = f"http://127.0.0.1:{live_server}/mcp"
        client = MCPClient(StreamableHttpTransport(url))

        # Simulate the reentrant pattern that produces counter=3.
        await client.__aenter__()  # counter = 1
        await client.__aenter__()  # counter = 2
        await client.__aenter__()  # counter = 3
        assert self._counter(client) == 3

        await self._kill_session(client)
        assert self._counter(client) == 3  # stuck

        # connect() detects the stuck state, resets it, and reconnects cleanly.
        await client.connect()
        assert self._counter(client) == 1
        assert not self._task_done(client)

        # The server is actually reachable after recovery.
        result = await client.call_tool("echo", {"message": "recovered"})
        assert result is not None

        await client.close()

    @pytest.mark.asyncio
    async def test_connect_does_not_disturb_healthy_session(
        self, live_server: int
    ) -> None:
        """connect() when the session is alive should increment counter normally."""
        url = f"http://127.0.0.1:{live_server}/mcp"
        client = MCPClient(StreamableHttpTransport(url))

        await client.connect()
        assert self._counter(client) == 1
        assert not self._task_done(client)

        # Reentrant connect on a live session must NOT trigger close().
        await client.connect()
        assert self._counter(client) == 2  # incremented, not reset
        assert not self._task_done(client)

        await client.close()
        assert self._counter(client) == 0
