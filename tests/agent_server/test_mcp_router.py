"""Tests for mcp_router.py endpoints."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Generator
from types import SimpleNamespace

import anyio
import pytest
from deprecation import DeprecatedWarning
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.mcp_router import (
    _OAUTH_PROBE_JOB_TTL_SECONDS,
    MCPTestRequest,
    _BrowserCoordinatedOAuth,
    _MCPOAuthProbeJob,
    _oauth_probe_jobs,
    _oauth_probe_jobs_lock,
    _register_oauth_job,
)
from openhands.agent_server.persistence import reset_stores
from openhands.sdk.mcp.config import MCPServer, to_fastmcp_mcp_config

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
def client() -> Generator[TestClient]:
    reset_stores()
    config = Config(session_api_keys=[])  # Disable authentication.
    with TestClient(create_app(config), raise_server_exceptions=False) as test_client:
        yield test_client
    reset_stores()


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
                "transport": "http",
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


def test_mcp_test_http_transport_is_accepted(
    client: TestClient, http_mcp_server: MCPTestServer
):
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
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
                "transport": "stdio",
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
                "transport": "http",
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
                "transport": "http",
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
                "transport": "http",
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
                "transport": "stdio",
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
    seen_configs: list[dict[str, MCPServer]] = []

    class FakeClient:
        def __init__(self):
            self.tools: list[object] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_create_mcp_tools(
        config: dict[str, MCPServer],
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
                "transport": "http",
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
    assert [to_fastmcp_mcp_config(config) for config in seen_configs] == [
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
                "transport": "stdio",
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
                "transport": "http",
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
        json={"server": {"transport": "stdio", "command": ""}},
    )
    assert response.status_code == 422


def test_mcp_test_rejects_unknown_transport(client: TestClient):
    response = client.post(
        "/api/mcp/test",
        json={"server": {"transport": "websocket", "url": "ws://example.com"}},
    )
    assert response.status_code == 422


def test_mcp_test_clamps_timeout_range(client: TestClient):
    """Timeout must be > 0 and <= 120; 0 should be rejected at the schema layer."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {"transport": "stdio", "command": "true"},
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
                "transport": "http",
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
                "transport": "http",
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
                "transport": "http",
                "url": "https://example.com/mcp",
                "auth": {"strategy": "bearer", "value": "some-token"},
                "headers": {"Authorization": "Bearer other-token"},
            },
            "timeout": 5.0,
        },
    )
    assert response.status_code == 422


def test_mcp_test_accepts_legacy_remote_api_key_field_as_bearer():
    with pytest.warns(DeprecatedWarning, match="_RemoteMCPServerSpec\\.api_key"):
        request = MCPTestRequest.model_validate(
            {
                "server": {
                    "transport": "http",
                    "url": "https://example.com/mcp",
                    "api_key": "some-token",
                },
                "timeout": 5.0,
            }
        )

    auth = request.resolved_server.auth
    assert auth is not None
    assert auth.strategy == "bearer"
    assert auth.value is not None
    assert auth.value.get_secret_value() == "some-token"


def test_mcp_test_rejects_legacy_api_key_with_auth(client: TestClient):
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
                "url": "https://example.com/mcp",
                "api_key": "some-token",
                "auth": {"strategy": "bearer", "value": "other-token"},
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
                "transport": "http",
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
                "transport": "http",
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


def test_mcp_test_returns_encrypted_oauth_state_from_probe(
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
        asyncio.run(
            mcp_oauth_token_storage.put(
                key="https://mcp.example.com/mcp/client_info",
                value={
                    "redirect_uris": ["http://127.0.0.1:64801/callback"],
                    "client_id": "superhuman-client",
                    "client_secret": "superhuman-client-secret",
                },
                collection="mcp-oauth-client-info",
            )
        )
        asyncio.run(
            mcp_oauth_token_storage.put(
                key="https://mcp.example.com/mcp/token_expiry",
                value={"expires_at": 12345.0},
                collection="mcp-oauth-token-expiry",
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
                "transport": "http",
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
    assert "server" not in body
    oauth_state = body["oauth_state"]
    oauth_value = oauth_state["tokens"]
    assert oauth_value["access_token"].startswith("gAAAA")
    assert oauth_value["refresh_token"].startswith("gAAAA")
    assert oauth_value["access_token"] != "oauth-access-token"
    assert oauth_state["client_info"]["client_id"] == "superhuman-client"
    assert oauth_state["client_info"]["client_secret"].startswith("gAAAA")
    assert oauth_state["token_expires_at"] == 12345.0


def test_mcp_oauth_start_returns_authorization_url_and_final_state(
    monkeypatch: pytest.MonkeyPatch,
):
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    client = TestClient(create_app(config), raise_server_exceptions=False)

    class FakeOAuth:
        def __init__(self, job):
            self.job = job

        async def redirect_handler(self, authorization_url: str) -> None:
            self.job.set_authorization_url(authorization_url)

    def fake_oauth_from_authentication(authentication, *, oauth_token_storage, job):
        assert authentication.client_id == "notion-client"
        assert authentication.client_secret.get_secret_value() == "notion-secret"
        return FakeOAuth(job)

    class FakeClient:
        def __init__(self):
            self.tools = [SimpleNamespace(name="notion_lookup")]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_create_mcp_tools(
        config,
        timeout=30.0,
        *,
        mcp_oauth_token_storage=None,
        mcp_oauth_factory=None,
    ):
        assert mcp_oauth_token_storage is not None
        assert mcp_oauth_factory is not None
        server_name, server = next(iter(config.items()))
        oauth = mcp_oauth_factory(
            server_name,
            server,
            server.auth,
            mcp_oauth_token_storage,
        )
        asyncio.run(
            oauth.redirect_handler(
                "https://oauth.example.com/authorize?state=test-state"
            )
        )
        asyncio.run(
            mcp_oauth_token_storage.put(
                key="https://mcp.example.com/mcp/tokens",
                value={"access_token": "oauth-access-token"},
                collection="mcp-oauth-token",
            )
        )
        return FakeClient()

    monkeypatch.setattr(
        "openhands.agent_server.mcp_router._oauth_auth_from_authentication",
        fake_oauth_from_authentication,
    )
    monkeypatch.setattr(
        "openhands.agent_server.mcp_router.create_mcp_tools",
        fake_create_mcp_tools,
    )

    response = client.post(
        "/api/mcp/oauth/start",
        json={
            "server": {
                "transport": "http",
                "url": "https://mcp.example.com/mcp",
                "auth": {
                    "strategy": "oauth2",
                    "authentication": {
                        "type": "oauth",
                        "client_auth_method": "client_secret_post",
                        "client_id": "notion-client",
                        "client_secret": "notion-secret",
                    },
                },
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    start_body = response.json()
    assert start_body["ok"] is True
    assert start_body["authorization_url"].startswith("https://oauth.example.com/")

    status_body = None
    for _ in range(20):
        status_response = client.get(f"/api/mcp/oauth/status/{start_body['job_id']}")
        assert status_response.status_code == 200, status_response.text
        status_body = status_response.json()
        if status_body["status"] == "succeeded":
            break
        time.sleep(0.05)

    assert status_body is not None
    assert status_body["ok"] is True
    assert status_body["status"] == "succeeded"
    assert status_body["tools"] == ["notion_lookup"]
    access_token = status_body["oauth_state"]["tokens"]["access_token"]
    assert access_token.startswith("gAAAA")
    assert access_token != "oauth-access-token"


def test_browser_coordinated_oauth_callback_handler_uses_fastmcp_callback_api(
    monkeypatch: pytest.MonkeyPatch,
):
    job = _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
        ),
        cipher=None,
    )
    oauth = _BrowserCoordinatedOAuth(
        job=job,
        mcp_url="https://mcp.example.com/mcp",
        callback_port=64801,
    )
    calls: list[dict[str, object]] = []

    class FakeServer:
        should_exit = False

        def __init__(self, *, result_container, result_ready):
            self.result_container = result_container
            self.result_ready = result_ready

        async def serve(self):
            self.result_container.code = "oauth-code"
            self.result_container.state = "oauth-state"
            self.result_ready.set()
            while not self.should_exit:
                await anyio.sleep(0.01)

    def fake_create_oauth_callback_server(**kwargs):
        calls.append(kwargs)
        return FakeServer(
            result_container=kwargs["result_container"],
            result_ready=kwargs["result_ready"],
        )

    monkeypatch.setattr(
        "openhands.agent_server.mcp_router.create_oauth_callback_server",
        fake_create_oauth_callback_server,
    )

    code, state = asyncio.run(oauth.callback_handler())

    assert (code, state) == ("oauth-code", "oauth-state")
    assert job.callback_ready.is_set()
    assert job.callback_url == "http://localhost:64801/callback"
    assert calls == [
        {
            "port": 64801,
            "server_url": "https://mcp.example.com/mcp",
            "result_container": calls[0]["result_container"],
            "result_ready": calls[0]["result_ready"],
        }
    ]
    assert "host" not in calls[0]


def test_mcp_oauth_callback_rejects_unknown_job(client: TestClient):
    response = client.post(
        "/api/mcp/oauth/callback/not-a-job",
        json={"callback_url": "http://127.0.0.1:12345/callback?code=x&state=y"},
    )

    assert response.status_code == 404


def test_register_oauth_job_sweeps_expired_jobs():
    old_job = _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
        ),
        cipher=None,
    )
    old_job.created_at -= _OAUTH_PROBE_JOB_TTL_SECONDS + 1
    new_job = _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
        ),
        cipher=None,
    )
    try:
        with _oauth_probe_jobs_lock:
            _oauth_probe_jobs.clear()
            _oauth_probe_jobs[old_job.id] = old_job
        _register_oauth_job(new_job)

        with _oauth_probe_jobs_lock:
            assert old_job.id not in _oauth_probe_jobs
            assert new_job.id in _oauth_probe_jobs
    finally:
        with _oauth_probe_jobs_lock:
            _oauth_probe_jobs.clear()


def test_mcp_test_rejects_legacy_top_level_oauth_authentication(client: TestClient):
    """OAuth metadata now belongs inside the OAuth auth credential."""
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
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
