"""Tests for mcp_router.py endpoints."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import time
from collections.abc import Generator
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.mcp_router import (
    _OAUTH_PROBE_JOB_TTL_SECONDS,
    SECRET_MASK,
    MCPTestFailure,
    MCPTestRequest,
    MCPTestSuccess,
    _BrowserCoordinatedOAuth,
    _MCPOAuthProbeJob,
    _oauth_probe_jobs,
    _oauth_probe_jobs_lock,
    _register_oauth_job,
)
from openhands.agent_server.persistence import get_secrets_store, reset_stores
from openhands.agent_server.persistence.store import FileSecretsStore
from openhands.sdk.mcp.config import to_fastmcp_mcp_config
from tests.agent_server.conftest import FakeMCPTools

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
    fake_mcp_tools: FakeMCPTools,
):
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    cipher = config.cipher
    assert cipher is not None
    client = TestClient(create_app(config), raise_server_exceptions=False)

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
    assert [to_fastmcp_mcp_config(config) for config in fake_mcp_tools.configs] == [
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
# ${NAME} secret references
# ---------------------------------------------------------------------------


def test_mcp_test_expands_remote_secret_reference_from_saved_secrets(
    fake_mcp_tools: FakeMCPTools,
):
    """A remote server saved with ``Bearer ${NAME}`` connects with the saved
    secret, as a conversation would, instead of sending the placeholder."""
    config = Config(session_api_keys=[])
    get_secrets_store(config).set_secret("SONAR_TOKEN", "sonar-saved-token")
    client = TestClient(create_app(config), raise_server_exceptions=False)

    response = client.post(
        "/api/mcp/test",
        json={
            "name": "sonar",
            "server": {
                "transport": "http",
                "url": "https://sonar.example/mcp",
                "auth": {"strategy": "bearer", "value": "${SONAR_TOKEN}"},
            },
            "timeout": 10.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    [seen] = fake_mcp_tools.configs
    assert to_fastmcp_mcp_config(seen)["mcpServers"]["sonar"]["auth"] == (
        "sonar-saved-token"
    )


def test_mcp_test_expands_references_like_a_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    """Saved secrets, then the environment, then ``:-`` defaults. The saved
    secret is masked in the returned tool output; the short environment
    value is not."""
    config = Config(session_api_keys=[])
    get_secrets_store(config).set_secret("HF_BOT_TOKEN", "xoxb-saved-secret")
    monkeypatch.setenv("HF_TEAM_ID", "T0123")
    monkeypatch.delenv("HF_CHANNEL", raising=False)
    client = TestClient(create_app(config), raise_server_exceptions=False)
    script = (
        "import json, os\n"
        "from fastmcp import FastMCP\n"
        "mcp = FastMCP('env-echo')\n"
        "@mcp.tool()\n"
        "def read_env() -> str:\n"
        "    token = os.environ.get('SLACK_BOT_TOKEN')\n"
        "    return json.dumps({\n"
        "        'bot_token': token,\n"
        "        'bot_token_ok': token == 'xoxb-saved-secret',\n"
        "        'team_id': os.environ.get('SLACK_TEAM_ID'),\n"
        "        'channel': os.environ.get('SLACK_CHANNEL'),\n"
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
                    "SLACK_BOT_TOKEN": "${HF_BOT_TOKEN}",
                    "SLACK_TEAM_ID": "${HF_TEAM_ID}",
                    "SLACK_CHANNEL": "${HF_CHANNEL:-general}",
                },
            },
            "timeout": 20.0,
            "tool_call": {"name": "read_env"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body
    assert json.loads(body["tool_result"]["text"]) == {
        "bot_token": SECRET_MASK,
        "bot_token_ok": True,
        "team_id": "T0123",
        "channel": "general",
    }


def _fail_with_expanded_url(fake_mcp_tools: FakeMCPTools) -> None:
    """Make the probe fail the way httpx does: echoing the (encoded) URL."""

    def fail(config, kwargs):
        [server] = config.values()
        raise RuntimeError(f"401 Unauthorized for url '{httpx.URL(server.url)}'")

    fake_mcp_tools.on_create = fail


@pytest.mark.parametrize(
    "secret, url",
    [
        ("super-secret-value-12345", "https://mcp.example/mcp?token=${URL_TOKEN}"),
        ("abc def", "https://mcp.example/mcp?token=${URL_TOKEN}"),
        # httpx keeps ``:`` but encodes ``@`` in userinfo: a partial encoding.
        ("user:Tr0ub4dor@3", "https://${URL_TOKEN}@mcp.example/mcp"),
    ],
    ids=["plain", "encoded", "userinfo"],
)
def test_mcp_test_masks_substituted_secrets_in_errors_and_logs(
    fake_mcp_tools: FakeMCPTools,
    caplog: pytest.LogCaptureFixture,
    secret: str,
    url: str,
):
    config = Config(session_api_keys=[])
    get_secrets_store(config).set_secret("URL_TOKEN", secret)
    client = TestClient(create_app(config), raise_server_exceptions=False)
    _fail_with_expanded_url(fake_mcp_tools)

    with caplog.at_level(logging.INFO, logger="openhands.agent_server.mcp_router"):
        response = client.post(
            "/api/mcp/test",
            json={"server": {"transport": "http", "url": url}, "timeout": 5.0},
        )

    error = response.json()["error"]
    # Decoding exposes the secret in any (partial) percent-encoding.
    assert secret not in unquote(error)
    assert secret not in unquote(caplog.text)
    # The rest of the message stays readable.
    assert "401 Unauthorized for url 'https://" in error
    assert "mcp.example/mcp" in error
    [record] = [r for r in caplog.records if "failed unexpectedly" in r.getMessage()]
    # A traceback that needs masking can only be logged as text.
    assert record.exc_info is None


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.example/mcp?q=${HF_ENV_TOKEN}",
        "https://mcp.example/${HF_ENV_TOKEN}/mcp",
    ],
    ids=["query", "path"],
)
def test_mcp_test_masks_environment_values_in_errors_and_logs(
    client: TestClient,
    fake_mcp_tools: FakeMCPTools,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
):
    """A value from the server's environment is masked like a saved secret,
    whatever the query key or position it is substituted into."""
    monkeypatch.setenv("HF_ENV_TOKEN", "env-super-secret-999")
    _fail_with_expanded_url(fake_mcp_tools)

    with caplog.at_level(logging.INFO, logger="openhands.agent_server.mcp_router"):
        response = client.post(
            "/api/mcp/test",
            json={"server": {"transport": "http", "url": url}, "timeout": 5.0},
        )

    assert "env-super-secret-999" not in response.text
    assert "env-super-secret-999" not in caplog.text
    assert SECRET_MASK in response.json()["error"]


def test_mcp_test_leaves_short_environment_values_readable(
    client: TestClient,
    fake_mcp_tools: FakeMCPTools,
    monkeypatch: pytest.MonkeyPatch,
):
    """Short environment values such as a port or a flag are not masked, so
    they do not mangle every ``80`` or ``1`` in the returned error."""
    monkeypatch.setenv("HF_PORT", "80")
    monkeypatch.setenv("HF_DEBUG", "1")
    _fail_with_expanded_url(fake_mcp_tools)

    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
                "url": "https://mcp.example:${HF_PORT}/v1801/mcp?debug=${HF_DEBUG}",
            },
            "timeout": 5.0,
        },
    )

    assert response.json()["error"] == (
        "RuntimeError: 401 Unauthorized for url "
        "'https://mcp.example:80/v1801/mcp?debug=1'"
    )


def test_mcp_test_masks_an_expanded_basic_credential(
    fake_mcp_tools: FakeMCPTools, caplog: pytest.LogCaptureFixture
):
    """Basic auth goes out as ``base64(user:password)``; that form is masked
    too, not only the substituted password."""
    config = Config(session_api_keys=[])
    get_secrets_store(config).set_secret("HF_DB_PASS", "s3cret-pw")
    client = TestClient(create_app(config), raise_server_exceptions=False)
    token = base64.b64encode(b"user:s3cret-pw").decode()

    def fail(config, kwargs):
        headers = to_fastmcp_mcp_config(config)["mcpServers"]["db"]["headers"]
        raise RuntimeError(f"rejected Authorization: {headers['Authorization']}")

    fake_mcp_tools.on_create = fail

    with caplog.at_level(logging.INFO, logger="openhands.agent_server.mcp_router"):
        response = client.post(
            "/api/mcp/test",
            json={
                "name": "db",
                "server": {
                    "transport": "http",
                    "url": "https://mcp.example/mcp",
                    "auth": {
                        "strategy": "basic",
                        "username": "user",
                        "password": "${HF_DB_PASS}",
                    },
                },
                "timeout": 5.0,
            },
        )

    error = response.json()["error"]
    assert "rejected Authorization: " in error
    for value in (token, "s3cret-pw"):
        assert value not in response.text
        assert value not in caplog.text


def test_mcp_test_logs_a_clean_unexpected_failure_with_exc_info(
    client: TestClient,
    fake_mcp_tools: FakeMCPTools,
    caplog: pytest.LogCaptureFixture,
):
    def fail(config, kwargs):
        raise RuntimeError("boom")

    fake_mcp_tools.on_create = fail

    with caplog.at_level(logging.WARNING, logger="openhands.agent_server.mcp_router"):
        response = client.post(
            "/api/mcp/test",
            json={
                "server": {"transport": "http", "url": "https://mcp.example/mcp"},
                "timeout": 5.0,
            },
        )

    assert response.json() == {
        "ok": False,
        "error": "RuntimeError: boom",
        "error_kind": "unknown",
    }
    [record] = [r for r in caplog.records if "failed unexpectedly" in r.getMessage()]
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError


def test_mcp_test_redacts_a_token_typed_into_the_url(
    client: TestClient,
    fake_mcp_tools: FakeMCPTools,
    caplog: pytest.LogCaptureFixture,
):
    """Not a reference, so nothing was substituted; the shared text redaction
    still keeps a credential-shaped query value out of the error and log."""
    _fail_with_expanded_url(fake_mcp_tools)

    with caplog.at_level(logging.WARNING, logger="openhands.agent_server.mcp_router"):
        response = client.post(
            "/api/mcp/test",
            json={
                "server": {
                    "transport": "http",
                    "url": "https://mcp.example/mcp?api_key=typed-key-123",
                },
                "timeout": 5.0,
            },
        )

    assert "typed-key-123" not in response.text
    assert "typed-key-123" not in caplog.text
    assert "mcp.example/mcp?api_key=<redacted>" in response.json()["error"]


def test_mcp_test_keeps_brace_text_that_expansion_leaves_literal(
    monkeypatch: pytest.MonkeyPatch, fake_mcp_tools: FakeMCPTools
):
    """``${...}`` text from a secret's value or a default is not a reference
    the user wrote: like a conversation, pass it on instead of failing."""
    config = Config(session_api_keys=[])
    store = get_secrets_store(config)
    store.set_secret("PASSWORD", "a-${OTHER}-b")
    store.set_secret("ENDPOINT", "https://h/${path}/mcp")
    store.set_secret("B", "set")
    monkeypatch.delenv("A", raising=False)
    client = TestClient(create_app(config), raise_server_exceptions=False)

    response = client.post(
        "/api/mcp/test",
        json={
            "name": "braces",
            "server": {
                "transport": "http",
                "url": "https://mcp.example/mcp",
                "headers": {
                    "X-Password": "${PASSWORD}",
                    "X-Endpoint": "${ENDPOINT}",
                    "X-Nested": "${A:-${B}}",
                },
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True, response.json()
    [seen] = fake_mcp_tools.configs
    headers = to_fastmcp_mcp_config(seen)["mcpServers"]["braces"]["headers"]
    assert headers == {
        "X-Password": "a-${OTHER}-b",
        "X-Endpoint": "https://h/${path}/mcp",
        "X-Nested": "${B}",
    }


def test_mcp_test_names_an_unset_secret_reference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("HF_MISSING_TOKEN", raising=False)

    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
                "url": "http://127.0.0.1:9/mcp",
                "headers": {"Authorization": "Bearer ${HF_MISSING_TOKEN}"},
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert "HF_MISSING_TOKEN" in body["error"]
    # Settings UIs mask the typed credential, i.e. the reference itself.
    assert "${HF_MISSING_TOKEN}" not in body["error"]


def test_mcp_test_expands_an_oauth_server_but_sends_its_auth_as_written(
    fake_mcp_tools: FakeMCPTools,
):
    """The OAuth client copies a configured client secret into the state the
    caller saves, so ``auth`` goes out as written while the rest expands."""
    config = Config(session_api_keys=[])
    store = get_secrets_store(config)
    store.set_secret("HF_TEAM", "team-42")
    store.set_secret("HF_CLIENT_SECRET", "oauth-client-secret")
    client = TestClient(create_app(config), raise_server_exceptions=False)

    response = client.post(
        "/api/mcp/test",
        json={
            "name": "notion",
            "server": {
                "transport": "http",
                "url": "https://mcp.example/mcp",
                "headers": {"X-Team": "${HF_TEAM}"},
                "auth": {
                    "strategy": "oauth2",
                    "authentication": {
                        "type": "oauth",
                        "client_auth_method": "client_secret_post",
                        "client_id": "notion-client",
                        "client_secret": "${HF_CLIENT_SECRET}",
                    },
                },
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True, response.json()
    [seen] = fake_mcp_tools.configs
    server = seen["notion"]
    assert server.headers is not None
    assert server.headers["X-Team"].get_secret_value() == "team-42"
    oauth_auth = server.oauth_auth
    assert oauth_auth is not None and oauth_auth.authentication is not None
    client_secret = oauth_auth.authentication.client_secret
    assert client_secret is not None
    assert client_secret.get_secret_value() == "${HF_CLIENT_SECRET}"


@pytest.mark.parametrize(
    "url, environment",
    [("${HF_BASE_URL}", {"HF_BASE_URL": ""}), ("${HF_BASE_URL:-}", {})],
    ids=["empty-value", "empty-default"],
)
def test_mcp_test_reports_a_config_that_is_invalid_once_expanded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    environment: dict[str, str],
):
    monkeypatch.delenv("HF_BASE_URL", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    response = client.post(
        "/api/mcp/test",
        json={"server": {"transport": "http", "url": url}, "timeout": 5.0},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "unknown"
    assert "url" in body["error"]


def test_mcp_test_names_a_reference_after_a_malformed_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """``${X:-oops`` is not a reference; the ``${HF_REQ}`` after it still is."""
    monkeypatch.delenv("HF_REQ", raising=False)

    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
                "url": "https://mcp.example/mcp",
                "headers": {"A": "${X:-oops", "B": "${HF_REQ}"},
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert "HF_REQ" in body["error"]


def test_mcp_test_keeps_the_secret_out_of_a_post_expansion_validation_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """A substituted header name of ``Authorization`` next to ``auth`` fails
    validation; pydantic's own message would echo the expanded config."""
    config = Config(session_api_keys=[])
    get_secrets_store(config).set_secret("HF_TOKEN", "REAL-SECRET-VALUE-123")
    monkeypatch.setenv("HF_HEADER", "Authorization")
    client = TestClient(create_app(config), raise_server_exceptions=False)

    with caplog.at_level(logging.INFO, logger="openhands.agent_server.mcp_router"):
        response = client.post(
            "/api/mcp/test",
            json={
                "server": {
                    "transport": "http",
                    "url": "https://mcp.example/mcp",
                    "headers": {"${HF_HEADER}": "x"},
                    "auth": {"strategy": "bearer", "value": "${HF_TOKEN}"},
                },
                "timeout": 5.0,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    # The header name came from the environment, so it is masked too.
    assert "cannot be combined with an explicit top-level" in body["error"]
    assert "REAL-SECRET-VALUE-123" not in response.text
    assert "REAL-SECRET-VALUE-123" not in caplog.text


def test_mcp_test_reports_an_unreadable_secrets_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def deny(self, *, strict=False):
        raise PermissionError(13, "Permission denied", "secrets.json")

    monkeypatch.setattr(FileSecretsStore, "load", deny)

    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
                "url": "https://mcp.example/mcp",
                "auth": {"strategy": "bearer", "value": "${HF_TOKEN}"},
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"].startswith("Could not read the saved secrets")


def test_mcp_test_reads_the_secrets_file_once_per_name(
    monkeypatch: pytest.MonkeyPatch, fake_mcp_tools: FakeMCPTools
):
    config = Config(session_api_keys=[])
    get_secrets_store(config).set_secret("HF_TOKEN", "tok-value")
    client = TestClient(create_app(config), raise_server_exceptions=False)
    loads: list[str] = []
    real_load = FileSecretsStore.load

    def counting_load(self, *, strict=False):
        loads.append("load")
        return real_load(self, strict=strict)

    monkeypatch.setattr(FileSecretsStore, "load", counting_load)

    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "http",
                "url": "https://mcp.example/mcp",
                "headers": {"X-A": "${HF_TOKEN}", "X-B": "${HF_TOKEN}"},
                "auth": {"strategy": "bearer", "value": "${HF_TOKEN}"},
            },
            "timeout": 5.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True, response.json()
    assert len(loads) == 1


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
    fake_mcp_tools: FakeMCPTools,
):
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    client = TestClient(create_app(config), raise_server_exceptions=False)

    def store_tokens(config, kwargs):
        mcp_oauth_token_storage = kwargs["mcp_oauth_token_storage"]
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

    fake_mcp_tools.on_create = store_tokens

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
    assert len(fake_mcp_tools.calls) == 1
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
    monkeypatch: pytest.MonkeyPatch, fake_mcp_tools: FakeMCPTools
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

    def run_oauth(config, kwargs):
        mcp_oauth_token_storage = kwargs["mcp_oauth_token_storage"]
        mcp_oauth_factory = kwargs["mcp_oauth_factory"]
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

    monkeypatch.setattr(
        "openhands.agent_server.mcp_router._oauth_auth_from_authentication",
        fake_oauth_from_authentication,
    )
    fake_mcp_tools.tools = ["notion_lookup"]
    fake_mcp_tools.on_create = run_oauth

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


def test_mcp_oauth_start_succeeds_without_consent_when_stored_tokens_work(
    fake_mcp_tools: FakeMCPTools,
):
    # Arrange: the probe connects with the stored tokens; FastMCP never
    # publishes an authorization URL.
    config = Config(session_api_keys=[], secret_key=SecretStr("test-secret-key"))
    client = TestClient(create_app(config), raise_server_exceptions=False)
    fake_mcp_tools.tools = ["notion_lookup"]

    # Act
    started = time.monotonic()
    response = client.post(
        "/api/mcp/oauth/start",
        json={
            "server": {
                "transport": "http",
                "url": "https://mcp.example.com/mcp",
                "auth": {
                    "strategy": "oauth2",
                    "state": {"tokens": {"access_token": "stored-access-token"}},
                },
            },
            "timeout": 10.0,
        },
    )
    elapsed = time.monotonic() - started

    # Assert: success with only the job id, without waiting for the consent
    # timeout; the outcome is on the status route.
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert response.json() == {"ok": True, "job_id": job_id}
    assert elapsed < 5.0
    status_body = client.get(f"/api/mcp/oauth/status/{job_id}").json()
    assert status_body["status"] == "succeeded"
    assert status_body["tools"] == ["notion_lookup"]


def test_browser_coordinated_oauth_callback_handler_delegates_to_fastmcp(
    monkeypatch: pytest.MonkeyPatch,
):
    """Return FastMCP's result untouched; re-wrapping it broke mcp 2.x."""
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

    sentinel = object()
    seen: list[bool] = []

    async def fake_base_callback_handler(self):
        # The URL must be published before the base blocks on the callback,
        # otherwise the frontend never learns where to send the browser.
        seen.append(job.callback_ready.is_set())
        return sentinel

    monkeypatch.setattr(
        "fastmcp.client.auth.oauth.OAuth.callback_handler",
        fake_base_callback_handler,
    )

    result = asyncio.run(oauth.callback_handler())

    assert result is sentinel
    assert seen == [True]
    assert job.callback_url == "http://localhost:64801/callback"


def test_mcp_oauth_callback_rejects_unknown_job(client: TestClient):
    response = client.post(
        "/api/mcp/oauth/callback/not-a-job",
        json={"callback_url": "http://127.0.0.1:12345/callback?code=x&state=y"},
    )

    assert response.status_code == 404


@pytest.fixture
def closed_callback_job() -> Generator[_MCPOAuthProbeJob]:
    """A registered job whose local callback server is no longer listening."""
    job = _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
        ),
        cipher=None,
    )
    job.set_authorization_url("https://oauth.example.com/authorize?state=s")
    job.set_callback_ready(f"http://localhost:{_find_free_port()}/callback")
    _register_oauth_job(job)
    try:
        yield job
    finally:
        with _oauth_probe_jobs_lock:
            _oauth_probe_jobs.pop(job.id, None)


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (MCPTestSuccess(tools=["echo"]), "succeeded"),
        (
            MCPTestFailure(error="Timed out", error_kind="timeout"),
            "failed",
        ),
    ],
)
def test_mcp_oauth_callback_replay_on_finished_job_returns_job_status(
    client: TestClient,
    closed_callback_job: _MCPOAuthProbeJob,
    result,
    expected_status: str,
):
    closed_callback_job.set_result(result)

    response = client.post(
        f"/api/mcp/oauth/callback/{closed_callback_job.id}",
        json={"callback_url": f"{closed_callback_job.callback_url}?code=x&state=s"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert closed_callback_job.status == expected_status


def test_mcp_oauth_callback_server_gone_on_live_job_returns_409(
    client: TestClient, closed_callback_job: _MCPOAuthProbeJob
):
    response = client.post(
        f"/api/mcp/oauth/callback/{closed_callback_job.id}",
        json={"callback_url": f"{closed_callback_job.callback_url}?code=x&state=s"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "OAuth callback server is not listening"
    assert closed_callback_job.status == "authorizing"


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


@pytest.mark.parametrize(
    ("callback_url", "detail"),
    [
        ("http://localhost:99999/callback?code=x", "Unexpected OAuth callback URL"),
        ("http://localhost:abc/callback?code=x", "Unexpected OAuth callback URL"),
        ("http://[::1/callback?code=x", "Invalid OAuth callback URL"),
    ],
)
def test_mcp_oauth_callback_rejects_malformed_url_with_400(
    client: TestClient, callback_url: str, detail: str
):
    job = _MCPOAuthProbeJob(
        request=MCPTestRequest.model_validate(
            {"server": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
        ),
        cipher=None,
    )
    job.set_authorization_url("https://auth.example.com/authorize")
    job.set_callback_ready("http://localhost:64801/callback")
    try:
        _register_oauth_job(job)
        response = client.post(
            f"/api/mcp/oauth/callback/{job.id}",
            json={"callback_url": callback_url},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == detail
        assert job.status == "authorizing"
    finally:
        with _oauth_probe_jobs_lock:
            _oauth_probe_jobs.clear()
