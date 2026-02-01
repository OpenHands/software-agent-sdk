"""Test that proves stateful MCP servers work with session persistence.

This test creates an MCP server with PER-SESSION state (keyed by session ID).
It verifies that:
1. The SDK keeps the same session across multiple tool calls
2. Authentication set via one tool is available to other tools
3. Session state is NOT lost between calls

This directly addresses the user's reported issue where session-based auth
was breaking because each tool call created a new session.

The key insight: With the OLD code, each `async with client:` would disconnect
on exit and reconnect on the next entry, creating a NEW session each time.
With the FIX, we call `__aenter__` once and keep the connection open.

Related: https://github.com/OpenHands/software-agent-sdk/issues/1739
"""

import asyncio
import socket
import threading
import time

import pytest
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.tool import MCPToolExecutor


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def stateful_server():
    """Fixture providing a per-session stateful MCP test server."""
    mcp = FastMCP("session-stateful-test-server")
    sessions: dict[str, dict] = {}

    @mcp.tool()
    def set_auth_token(token: str) -> str:
        """Set authentication token for this session."""
        ctx = get_context()
        session_id = ctx.session_id if ctx else "unknown"
        if session_id not in sessions:
            sessions[session_id] = {}
        sessions[session_id]["token"] = token
        return f"Session {session_id[:8]}: Auth token set to {token}"

    @mcp.tool()
    def get_auth_token() -> str:
        """Get the current auth token (proves session persistence)."""
        ctx = get_context()
        session_id = ctx.session_id if ctx else "unknown"
        token = sessions.get(session_id, {}).get("token")
        if token is None:
            return (
                f"Session {session_id[:8]}: ERROR - "
                "No auth token! Session state was lost!"
            )
        return f"Session {session_id[:8]}: Current auth token is {token}"

    @mcp.tool()
    def increment_counter() -> str:
        """Increment a per-session counter."""
        ctx = get_context()
        session_id = ctx.session_id if ctx else "unknown"
        if session_id not in sessions:
            sessions[session_id] = {"counter": 0}
        if "counter" not in sessions[session_id]:
            sessions[session_id]["counter"] = 0
        sessions[session_id]["counter"] += 1
        counter = sessions[session_id]["counter"]
        return f"Session {session_id[:8]}: Counter is now {counter}"

    @mcp.tool()
    def get_counter() -> str:
        """Get current counter value for this session."""
        ctx = get_context()
        session_id = ctx.session_id if ctx else "unknown"
        counter = sessions.get(session_id, {}).get("counter", 0)
        return f"Session {session_id[:8]}: Counter value is {counter}"

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
    yield sessions, port


class TestStatefulMCPSessionPersistence:
    """Tests proving that session-based MCP servers work correctly.

    These tests use a server that tracks state PER SESSION ID.
    If the SDK creates a new session for each tool call, the state is lost.
    The fix keeps the session open, preserving state across calls.
    """

    def test_counter_persists_across_calls(self, stateful_server):
        """Test that per-session counter persists across multiple tool calls.

        This is the CORE test - if sessions were being reset, the counter
        would reset to 0 between calls because each new session has no state.
        """
        sessions, port = stateful_server
        sessions.clear()

        config = {
            "mcpServers": {
                "stateful": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                }
            }
        }

        with create_mcp_tools(config, timeout=10.0) as client:
            increment_tool = next(t for t in client if t.name == "increment_counter")
            get_tool = next(t for t in client if t.name == "get_counter")

            executor = increment_tool.executor
            assert isinstance(executor, MCPToolExecutor)

            # Increment 3 times - all should use SAME session
            for i in range(3):
                action = increment_tool.action_from_arguments({})
                result = executor(action)
                assert f"Counter is now {i + 1}" in result.text

            # Verify counter is at 3 (not reset due to new session)
            get_executor = get_tool.executor
            assert isinstance(get_executor, MCPToolExecutor)
            action = get_tool.action_from_arguments({})
            result = get_executor(action)
            assert "Counter value is 3" in result.text

    def test_auth_token_persists_across_tools(self, stateful_server):
        """Test that authentication set in one call is available in subsequent calls.

        This simulates the user's exact use case: setting a token via set_token
        and then using it in subsequent operations. With the old code, each
        tool call created a new session, losing the auth token.
        """
        sessions, port = stateful_server
        sessions.clear()

        config = {
            "mcpServers": {
                "stateful": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                }
            }
        }

        with create_mcp_tools(config, timeout=10.0) as client:
            set_auth_tool = next(t for t in client if t.name == "set_auth_token")
            get_auth_tool = next(t for t in client if t.name == "get_auth_token")

            set_executor = set_auth_tool.executor
            get_executor = get_auth_tool.executor
            assert isinstance(set_executor, MCPToolExecutor)
            assert isinstance(get_executor, MCPToolExecutor)

            # Set auth token
            action = set_auth_tool.action_from_arguments({"token": "secret-123"})
            result = set_executor(action)
            assert "Auth token set to secret-123" in result.text

            # Verify auth token persists
            # WITH OLD CODE: This would fail with "ERROR - No auth token!"
            # WITH FIX: Same session is used, token is preserved
            action = get_auth_tool.action_from_arguments({})
            result = get_executor(action)

            # THE KEY ASSERTION: Token must still be there
            assert "secret-123" in result.text
            assert "ERROR" not in result.text  # No session reset error

    def test_multiple_operations_same_session(self, stateful_server):
        """Test a realistic workflow: authenticate, then perform multiple operations."""
        sessions, port = stateful_server
        sessions.clear()

        config = {
            "mcpServers": {
                "stateful": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                }
            }
        }

        with create_mcp_tools(config, timeout=10.0) as client:
            # Get all tools
            set_auth = next(t for t in client if t.name == "set_auth_token")
            get_auth = next(t for t in client if t.name == "get_auth_token")
            increment = next(t for t in client if t.name == "increment_counter")
            get_counter = next(t for t in client if t.name == "get_counter")

            # Verify executors exist
            assert set_auth.executor is not None
            assert get_auth.executor is not None
            assert increment.executor is not None
            assert get_counter.executor is not None

            # Simulate realistic workflow:
            # 1. Authenticate
            action = set_auth.action_from_arguments({"token": "my-api-key"})
            result = set_auth.executor(action)
            assert "my-api-key" in result.text

            # 2. Do some operations (all should use same session)
            for _ in range(5):
                action = increment.action_from_arguments({})
                increment.executor(action)

            # 3. Verify everything still works in same session
            action = get_counter.action_from_arguments({})
            result = get_counter.executor(action)
            assert "Counter value is 5" in result.text

            action = get_auth.action_from_arguments({})
            result = get_auth.executor(action)
            assert "my-api-key" in result.text  # Auth still there!
            assert "ERROR" not in result.text
