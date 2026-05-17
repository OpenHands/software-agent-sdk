"""Tests for mcp_router.py endpoints."""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config

# Reuse the real FastMCP-based test-server helper from the SDK tests; spinning
# up a real subprocess MCP server inside a unit test is unreliable across CI
# images (depends on npx, network, etc.), but an in-process FastMCP HTTP server
# is perfectly portable and exercises the same connect/list-tools code path
# the endpoint relies on.
from tests.sdk.mcp.test_create_mcp_tool import (  # noqa: E402
    MCPTestServer,
    _find_free_port,
)


@pytest.fixture
def client() -> TestClient:
    config = Config(session_api_keys=[])  # Disable authentication.
    return TestClient(create_app(config), raise_server_exceptions=False)


@pytest.fixture
def http_mcp_server():
    server = MCPTestServer("test-mcp-router")

    @server.add_tool
    def echo(message: str) -> str:
        """Echo a message back."""
        return message

    @server.add_tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    server.start(transport="http")
    yield server
    server.stop()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_mcp_test_remote_success(client: TestClient, http_mcp_server: MCPTestServer):
    """A reachable HTTP MCP server should report ok=True with the tool names."""
    response = client.post(
        "/api/mcp/test",
        json={
            "name": "happy-server",
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert set(body["tools"]) == {"echo", "add"}


def test_mcp_test_shttp_alias_is_accepted(
    client: TestClient, http_mcp_server: MCPTestServer
):
    """The OpenHands-specific 'shttp' transport alias should map to http."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "shttp",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


def test_mcp_test_stdio_success(client: TestClient):
    """A working stdio MCP server (FastMCP run via current python) should connect.

    We run a tiny FastMCP script via the current Python interpreter so the
    test stays hermetic (no npx, no network).
    """
    script = (
        "from fastmcp import FastMCP\n"
        "mcp = FastMCP('stdio-test')\n"
        "@mcp.tool()\n"
        "def ping() -> str:\n"
        "    return 'pong'\n"
        "mcp.run()\n"
    )

    response = client.post(
        "/api/mcp/test",
        json={
            "name": "stdio-happy",
            "server": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-c", script],
            },
            "timeout": 20.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body
    assert "ping" in body["tools"]


# ---------------------------------------------------------------------------
# Failure paths -- all should return HTTP 200 with ok=False
# ---------------------------------------------------------------------------


def test_mcp_test_stdio_failure_returns_structured_error(client: TestClient):
    """A bad stdio command should return ok=False with a useful error."""
    response = client.post(
        "/api/mcp/test",
        json={
            "name": "broken",
            "server": {
                "type": "stdio",
                "command": "/this/path/does/not/exist/definitely-not-a-binary",
                "args": [],
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] in {"connection", "timeout", "unknown"}
    assert body["error"], "expected a non-empty error message"


def test_mcp_test_remote_unreachable(client: TestClient):
    """Connecting to a port nothing is listening on should fail cleanly."""
    free_port = _find_free_port()
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{free_port}/mcp",
            },
            "timeout": 3.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] in {"connection", "timeout"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_mcp_test_rejects_empty_command(client: TestClient):
    response = client.post(
        "/api/mcp/test",
        json={"server": {"type": "stdio", "command": ""}},
    )
    assert response.status_code == 422


def test_mcp_test_rejects_unknown_transport(client: TestClient):
    response = client.post(
        "/api/mcp/test",
        json={"server": {"type": "websocket", "url": "ws://example.com"}},
    )
    assert response.status_code == 422


def test_mcp_test_clamps_timeout_range(client: TestClient):
    """Timeout must be > 0 and <= 120; 0 should be rejected at the schema layer."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {"type": "stdio", "command": "true"},
            "timeout": 0,
        },
    )
    assert response.status_code == 422


def test_mcp_test_bearer_token_in_auth_header(
    client: TestClient, http_mcp_server: MCPTestServer
):
    """Providing api_key should not break the connect (request must succeed)."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
                "api_key": "test-token-123",
            },
            "timeout": 10.0,
        },
    )

    # FastMCP's HTTP server doesn't enforce auth in this fixture, so the
    # request should still succeed; this guards against the api_key wiring
    # itself blowing up (e.g. malformed headers crashing the transport).
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
