"""Tests for the ACP auth-status probe router (GET /acp/auth-status)."""

import os
import tempfile
from base64 import urlsafe_b64encode
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_secrets_store,
    get_settings_store,
    reset_stores,
)
from openhands.sdk.agent.acp_agent import ACPAuthProbeResult
from openhands.sdk.settings import ACPAgentSettings
from openhands.sdk.settings.acp_providers import (
    ACP_CLI_AUTH_STATUS_ARGS,
    ACP_PROVIDERS,
)


PROBE_PATH = "openhands.agent_server.acp_auth_router.ACPAgent.probe_auth"
CLI_PROBE_PATH = "openhands.agent_server.acp_auth_router.ACPAgent.probe_cli_auth_status"


@pytest.fixture
def temp_persistence_dir():
    """Isolated persistence dir + fresh store singletons per test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        reset_stores()
        old_val = os.environ.get("OH_PERSISTENCE_DIR")
        os.environ["OH_PERSISTENCE_DIR"] = tmpdir
        yield Path(tmpdir)
        reset_stores()
        if old_val is not None:
            os.environ["OH_PERSISTENCE_DIR"] = old_val
        else:
            os.environ.pop("OH_PERSISTENCE_DIR", None)


@pytest.fixture
def config(temp_persistence_dir):
    return Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(urlsafe_b64encode(b"a" * 32).decode("ascii")),
    )


@pytest.fixture
def client(config):
    return TestClient(create_app(config))


def _result(
    *,
    authenticated: bool = True,
    auth_methods: list[str] | None = None,
    agent_name: str = "",
    agent_version: str = "",
) -> ACPAuthProbeResult:
    return ACPAuthProbeResult(
        authenticated=authenticated,
        auth_methods=auth_methods or [],
        agent_name=agent_name,
        agent_version=agent_version,
    )


def test_unknown_server_returns_422(client):
    response = client.get("/api/acp/auth-status", params={"server": "bogus"})
    assert response.status_code == 422
    assert "bogus" in response.json()["detail"]


def test_missing_server_param_returns_422(client):
    # ``server`` is a required query parameter.
    response = client.get("/api/acp/auth-status")
    assert response.status_code == 422


def test_authenticated_status(client):
    probe_result = _result(
        authenticated=True,
        auth_methods=["chatgpt", "openai-api-key"],
        agent_name="codex-acp",
        agent_version="2.0",
    )
    with patch(PROBE_PATH, return_value=probe_result) as mock_probe:
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    body = response.json()
    assert body["server"] == "codex"
    assert body["status"] == "authenticated"
    assert body["auth_methods"] == ["chatgpt", "openai-api-key"]
    assert body["agent_name"] == "codex-acp"
    assert body["agent_version"] == "2.0"
    assert body["detail"] is None
    mock_probe.assert_called_once()


def test_unauthenticated_status(client):
    with patch(PROBE_PATH, return_value=_result(authenticated=False)):
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unauthenticated"
    assert body["detail"] is None


def test_unknown_status_on_probe_error(client):
    with patch(PROBE_PATH, side_effect=RuntimeError("npx not found")):
        response = client.get("/api/acp/auth-status", params={"server": "gemini-cli"})

    # The probe failing to run is reported as 'unknown', not an HTTP error, so
    # the canvas gracefully falls back to the API-key fields.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert "RuntimeError" in body["detail"]
    assert "npx not found" in body["detail"]


def test_unknown_status_on_timeout(client):
    with patch(PROBE_PATH, side_effect=TimeoutError()):
        response = client.get("/api/acp/auth-status", params={"server": "codex"})
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_claude_code_authenticated_via_cli_status(client):
    # claude-agent-acp can't be classified by the handshake (it accepts
    # session/new unauthenticated), so the router uses the CLI auth-status
    # fallback — and must NOT call the handshake probe.
    cli_result = _result(authenticated=True, auth_methods=["claude.ai"])
    with (
        patch(PROBE_PATH) as handshake,
        patch(CLI_PROBE_PATH, return_value=cli_result) as cli,
    ):
        response = client.get("/api/acp/auth-status", params={"server": "claude-code"})

    assert response.status_code == 200
    body = response.json()
    assert body["server"] == "claude-code"
    assert body["status"] == "authenticated"
    assert body["auth_methods"] == ["claude.ai"]
    handshake.assert_not_called()
    # Invoked with the registry command + the claude CLI auth-status args.
    cli.assert_called_once()
    assert cli.call_args.args[0] == list(ACP_PROVIDERS["claude-code"].default_command)
    assert cli.call_args.args[1] == list(ACP_CLI_AUTH_STATUS_ARGS["claude-code"])


def test_claude_code_unauthenticated_via_cli_status(client):
    with (
        patch(PROBE_PATH),
        patch(CLI_PROBE_PATH, return_value=_result(authenticated=False)),
    ):
        response = client.get("/api/acp/auth-status", params={"server": "claude-code"})

    assert response.status_code == 200
    assert response.json()["status"] == "unauthenticated"


def test_claude_code_cli_status_error_returns_unknown(client):
    # If the CLI status command can't run / parse, fall back to 'unknown' so the
    # canvas shows the API-key fields rather than falsely claiming "not logged in".
    with (
        patch(PROBE_PATH),
        patch(CLI_PROBE_PATH, side_effect=RuntimeError("npx not found")),
    ):
        response = client.get("/api/acp/auth-status", params={"server": "claude-code"})

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_provider_with_no_strategy_returns_unknown(client):
    # A provider that neither supports the handshake nor has a CLI fallback
    # short-circuits to 'unknown' without spawning anything. Simulate by clearing
    # the CLI fallback for claude (whose handshake is already unsupported).
    with (
        patch("openhands.agent_server.acp_auth_router.ACP_CLI_AUTH_STATUS_ARGS", {}),
        patch(PROBE_PATH) as handshake,
        patch(CLI_PROBE_PATH) as cli,
    ):
        response = client.get("/api/acp/auth-status", params={"server": "claude-code"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert body["detail"]  # explains why it can't be probed
    handshake.assert_not_called()
    cli.assert_not_called()


def test_resolves_default_command_for_server(client):
    # With no ACP settings persisted, the command comes from the registry
    # default for the requested provider.
    with patch(PROBE_PATH, return_value=_result()) as mock_probe:
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    command = mock_probe.call_args.args[0]
    assert command == list(ACP_PROVIDERS["codex"].default_command)


def test_forwards_explicit_probe_timeout(client):
    # The router caps each probe explicitly so a hung CLI can't pin a threadpool
    # worker for longer than the configured ceiling.
    from openhands.sdk.agent.acp_agent import _ACP_AUTH_PROBE_TIMEOUT

    with patch(PROBE_PATH, return_value=_result()) as mock_probe:
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    assert mock_probe.call_args.kwargs["timeout"] == _ACP_AUTH_PROBE_TIMEOUT


def test_uses_persisted_acp_command_override(client, config):
    # A persisted ACPAgentSettings whose acp_server matches the probe is reused,
    # so a user's custom launch command is honored.
    get_settings_store(config).save(
        PersistedSettings(
            agent_settings=ACPAgentSettings(
                acp_server="codex",
                acp_command=["my-codex-acp", "--flag"],
            )
        )
    )
    with patch(PROBE_PATH, return_value=_result()) as mock_probe:
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    assert mock_probe.call_args.args[0] == ["my-codex-acp", "--flag"]


def test_includes_stored_secrets_in_probe_env(client, config):
    # Global secrets stored during onboarding are folded into the probe env so
    # the handshake authenticates exactly as a real conversation would.
    get_secrets_store(config).set_secret(name="OPENAI_API_KEY", value="sk-stored")

    with patch(PROBE_PATH, return_value=_result()) as mock_probe:
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    env = mock_probe.call_args.kwargs["env"]
    assert env["OPENAI_API_KEY"] == "sk-stored"


def test_settings_env_overrides_stored_secret(client, config):
    # When both a stored secret and the agent-settings provider env supply the
    # same var, the settings value wins (matches real conversation precedence).
    get_secrets_store(config).set_secret(name="OPENAI_API_KEY", value="sk-secret")
    get_settings_store(config).save(
        PersistedSettings(
            agent_settings=ACPAgentSettings(
                acp_server="codex",
                acp_env={"OPENAI_API_KEY": "sk-settings"},
            )
        )
    )
    with patch(PROBE_PATH, return_value=_result()) as mock_probe:
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    assert mock_probe.call_args.kwargs["env"]["OPENAI_API_KEY"] == "sk-settings"


def test_unknown_detail_scrubs_stored_secret(client, config):
    # If a provider error echoes a stored secret value, the 'unknown' detail
    # surfaced to the UI must redact it, not pass it through.
    get_secrets_store(config).set_secret(
        name="OPENAI_API_KEY", value="sk-supersecret-123"
    )
    with patch(
        PROBE_PATH,
        side_effect=RuntimeError("rejected key sk-supersecret-123"),
    ):
        response = client.get("/api/acp/auth-status", params={"server": "codex"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert "sk-supersecret-123" not in body["detail"]
    assert "***" in body["detail"]
