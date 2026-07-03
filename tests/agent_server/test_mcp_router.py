"""Tests for mcp_router.py endpoints."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

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


@pytest.fixture
def slack_like_mcp_server():
    """Server mimicking the Slack MCP server's error reporting.

    Upstream API failures come back as ordinary text content
    (``{"ok": false, "error": ...}``) with the MCP ``isError`` flag unset --
    the exact behavior that makes a tools/list-only probe a false positive
    for invalid credentials.
    """
    server = MCPTestServer("slack-like")

    @server.add_tool
    def slack_list_channels(limit: int = 100) -> str:
        """Return a Slack-style auth failure payload as plain content."""
        return json.dumps({"ok": False, "error": "invalid_auth"})

    @server.add_tool
    def boom() -> str:
        """Always raise so the call result carries isError=True."""
        raise RuntimeError("upstream exploded")

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
    # No tool_call requested -> no tool_result (back-compat with old clients).
    assert body.get("tool_result") is None


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
# Tool-call probe (credential verification)
# ---------------------------------------------------------------------------


def test_mcp_test_tool_call_reports_in_band_failure_payload(
    client: TestClient, slack_like_mcp_server: MCPTestServer
):
    """The requested tool runs and its payload is reported verbatim.

    Slack-style servers return upstream auth errors as ordinary content
    with isError unset; the endpoint must surface that payload (ok stays
    True -- interpreting it is the caller's job).
    """
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{slack_like_mcp_server.port}/mcp",
            },
            "timeout": 10.0,
            "tool_call": {"name": "slack_list_channels", "arguments": {"limit": 1}},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["tool_result"]["is_error"] is False
    assert "invalid_auth" in body["tool_result"]["text"]


def test_mcp_test_tool_call_handler_error_sets_is_error(
    client: TestClient, slack_like_mcp_server: MCPTestServer
):
    """A tool handler that raises is reported via the isError flag."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{slack_like_mcp_server.port}/mcp",
            },
            "timeout": 10.0,
            "tool_call": {"name": "boom"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["tool_result"]["is_error"] is True


def test_mcp_test_tool_call_unknown_tool_reported_without_invocation(
    client: TestClient, http_mcp_server: MCPTestServer
):
    """Requesting a tool the server doesn't advertise yields an errored
    tool_result naming the problem instead of a blind invocation."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
            },
            "timeout": 10.0,
            "tool_call": {"name": "definitely_not_a_tool"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["tool_result"]["is_error"] is True
    assert "not advertised" in body["tool_result"]["text"]


def test_mcp_test_decrypts_encrypted_env_values_before_spawn():
    """Fernet-encrypted env values round-tripped from settings are decrypted
    before the server process is spawned; plaintext values pass through.

    This is what lets the edit flow test the *stored* credentials even
    though the GUI only ever sees redacted placeholders.
    """
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    cipher = config.cipher
    assert cipher is not None
    client = TestClient(create_app(config), raise_server_exceptions=False)
    script = (
        "import json, os\n"
        "from fastmcp import FastMCP\n"
        "mcp = FastMCP('env-echo')\n"
        "@mcp.tool()\n"
        "def read_env() -> str:\n"
        "    return json.dumps({\n"
        "        'bot_token': os.environ.get('SLACK_BOT_TOKEN', ''),\n"
        "        'team_id': os.environ.get('SLACK_TEAM_ID', ''),\n"
        "    })\n"
        "mcp.run()\n"
    )

    response = client.post(
        "/api/mcp/test",
        json={
            "name": "env-echo",
            "server": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-c", script],
                "env": {
                    "SLACK_BOT_TOKEN": cipher.encrypt(SecretStr("xoxb-real-token")),
                    "SLACK_TEAM_ID": "T0123",
                },
            },
            "timeout": 20.0,
            "tool_call": {"name": "read_env"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body
    seen_env = json.loads(body["tool_result"]["text"])
    assert seen_env == {"bot_token": "xoxb-real-token", "team_id": "T0123"}


def test_mcp_test_decrypts_encrypted_remote_auth_before_connect(
    monkeypatch: pytest.MonkeyPatch,
):
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    cipher = config.cipher
    assert cipher is not None
    client = TestClient(create_app(config), raise_server_exceptions=False)
    seen_configs: list[dict] = []

    class FakeClient:
        def __init__(self):
            self.tools: list[object] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_create_mcp_tools(
        config,
        timeout=30.0,
        *,
        mcp_oauth_token_storage=None,
    ):
        seen_configs.append(config)
        return FakeClient()

    monkeypatch.setattr(
        "openhands.agent_server.mcp_router.create_mcp_tools",
        fake_create_mcp_tools,
    )

    response = client.post(
        "/api/mcp/test",
        json={
            "name": "linear",
            "server": {
                "type": "shttp",
                "url": "https://mcp.linear.app/mcp",
                "auth": {
                    "strategy": "bearer",
                    "value": cipher.encrypt(SecretStr("lin-real-token")),
                },
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert seen_configs == [
        {
            "mcpServers": {
                "linear": {
                    "url": "https://mcp.linear.app/mcp",
                    "transport": "http",
                    "auth": "lin-real-token",
                }
            }
        }
    ]


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


def test_mcp_test_bearer_token_in_auth_field(
    client: TestClient, http_mcp_server: MCPTestServer
):
    """Providing bearer-token auth should not break the connect."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
                "auth": {"strategy": "bearer", "value": "test-token-123"},
            },
            "timeout": 10.0,
        },
    )

    # FastMCP's HTTP server doesn't enforce auth in this fixture, so the
    # request should still succeed; this guards against the auth-field wiring
    # itself blowing up (e.g. malformed headers crashing the transport).
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# OAuth auth field
# ---------------------------------------------------------------------------


def test_mcp_test_accepts_oauth_auth_credential(
    client: TestClient, http_mcp_server: MCPTestServer
):
    """The OAuth auth credential should be accepted and forwarded to fastmcp.

    We can't complete a real OAuth handshake in a unit test, but we can verify
    the field is accepted at the schema layer and doesn't crash the request
    handler.  The local FastMCP test server doesn't require OAuth, so fastmcp
    will simply ignore the ``auth`` value and connect normally.
    """
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
                "auth": {"strategy": "oauth2"},
            },
            "timeout": 10.0,
        },
    )

    # The server doesn't enforce OAuth, so the connection should succeed.
    # (If fastmcp attempted a real OAuth flow it would fail because there's
    # no OAuth metadata on the test server — but fastmcp only starts the
    # flow when the server returns 401/403, which our test server won't.)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert set(body["tools"]) == {"echo", "add"}


def test_mcp_test_rejects_auth_with_auth_header(client: TestClient):
    """The FastMCP auth field is mutually exclusive with Authorization headers."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": "https://example.com/mcp",
                "auth": {"strategy": "bearer", "value": "some-token"},
                "headers": {"Authorization": "Bearer other-token"},
            },
            "timeout": 5.0,
        },
    )
    assert response.status_code == 422


def test_mcp_test_rejects_legacy_remote_api_key_field(client: TestClient):
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": "https://example.com/mcp",
                "api_key": "some-token",
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 422


def test_mcp_test_rejects_oauth_auth_with_auth_header(client: TestClient):
    """OAuth auth is mutually exclusive with a top-level Authorization header."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": "https://example.com/mcp",
                "auth": {"strategy": "oauth2"},
                "headers": {"Authorization": "Bearer some-token"},
            },
            "timeout": 5.0,
        },
    )
    assert response.status_code == 422


def test_mcp_test_accepts_explicit_oauth_authentication(
    client: TestClient, http_mcp_server: MCPTestServer
):
    """Structured OAuth metadata should round-trip through the test endpoint."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": f"http://127.0.0.1:{http_mcp_server.port}/mcp",
                "auth": {
                    "strategy": "oauth2",
                    "authentication": {
                        "type": "oauth",
                        "client_auth_method": "none",
                    },
                },
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert set(body["tools"]) == {"echo", "add"}


def test_mcp_test_returns_encrypted_oauth_credentials_from_probe(
    monkeypatch: pytest.MonkeyPatch,
):
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    client = TestClient(create_app(config), raise_server_exceptions=False)
    calls: list[object | None] = []

    class FakeClient:
        def __init__(self):
            self.tools: list[object] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_create_mcp_tools(
        config,
        timeout=30.0,
        *,
        mcp_oauth_token_storage=None,
    ):
        calls.append(mcp_oauth_token_storage)
        assert mcp_oauth_token_storage is not None
        asyncio.run(
            mcp_oauth_token_storage.put(
                key="https://mcp.example.com/mcp/tokens",
                value={
                    "access_token": "oauth-access-token",
                    "refresh_token": "oauth-refresh-token",
                },
                collection="mcp-oauth-token",
            )
        )
        return FakeClient()

    monkeypatch.setattr(
        "openhands.agent_server.mcp_router.create_mcp_tools",
        fake_create_mcp_tools,
    )

    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": "https://mcp.example.com/mcp",
                "auth": {
                    "strategy": "oauth2",
                    "authentication": {
                        "type": "oauth",
                        "client_auth_method": "none",
                    },
                },
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert len(calls) == 1
    assert body["server"]["auth"]["strategy"] == "oauth2"
    oauth_value = body["server"]["auth"]["credentials"]["mcp-oauth-token"][
        "https://mcp.example.com/mcp/tokens"
    ]["value"]
    assert oauth_value["access_token"].startswith("gAAAA")
    assert oauth_value["refresh_token"].startswith("gAAAA")
    assert oauth_value["access_token"] != "oauth-access-token"


def test_mcp_test_rejects_legacy_top_level_oauth_authentication(client: TestClient):
    """OAuth metadata now belongs inside the OAuth auth credential."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "type": "http",
                "url": "https://example.com/mcp",
                "authentication": {
                    "type": "oauth",
                    "client_auth_method": "none",
                },
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 422


def test_remote_spec_to_openhands_dict_includes_auth():
    """to_openhands_dict should preserve the tagged auth object."""
    from openhands.agent_server.mcp_router import _RemoteMCPServerSpec

    spec = _RemoteMCPServerSpec(
        type="shttp",
        url="https://mcp.example.com/mcp",
        auth={"strategy": "oauth2"},
    )
    d = spec.to_openhands_dict()
    assert d["auth"] == {"strategy": "oauth2"}
    assert d["transport"] == "http"  # shttp collapsed to http
    assert "headers" not in d


def test_remote_spec_to_openhands_dict_includes_authentication():
    """to_openhands_dict should keep OAuth authentication metadata under auth."""
    from openhands.agent_server.mcp_router import _RemoteMCPServerSpec

    spec = _RemoteMCPServerSpec.model_validate(
        {
            "type": "shttp",
            "url": "https://mcp.example.com/mcp",
            "auth": {
                "strategy": "oauth2",
                "authentication": {"type": "oauth", "client_auth_method": "none"},
            },
        }
    )
    d = spec.to_openhands_dict()
    assert d["auth"]["authentication"] == {
        "type": "oauth",
        "client_auth_method": "none",
    }


def test_remote_spec_to_openhands_dict_omits_auth_when_unset():
    """to_openhands_dict should omit auth when not set."""
    from openhands.agent_server.mcp_router import _RemoteMCPServerSpec

    spec = _RemoteMCPServerSpec(
        type="http",
        url="https://mcp.example.com/mcp",
    )
    d = spec.to_openhands_dict()
    assert "auth" not in d
