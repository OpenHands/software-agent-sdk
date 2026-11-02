"""Tests for ``POST /api/acp/models``."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server import acp_router as acp_router_module
from openhands.agent_server.acp_router import acp_router
from openhands.agent_server.config import Config
from openhands.agent_server.credential_binding import LocalVersionedCredentialBinding
from openhands.agent_server.local_secret_resolver import local_secret_resolution
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk.agent.acp_file_credentials import CODEX_AUTH_SECRET_NAME
from openhands.sdk.agent.acp_models import (
    ACPModelDiscovery,
    ACPModelDiscoveryError,
    ACPModelInfo,
)


FAKE_SERVER = Path(__file__).parents[1] / "sdk" / "agent" / "fake_acp_models_server.py"
FAKE_AGENT = {
    "agent_kind": "acp",
    "acp_server": "custom",
    "acp_command": [sys.executable, str(FAKE_SERVER)],
}


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(session_api_keys=[], conversations_path=tmp_path / "conversations")


@pytest.fixture
def client(config: Config) -> Iterator[TestClient]:
    app = FastAPI()
    app.state.config = config
    app.include_router(acp_router, prefix="/api")
    with local_secret_resolution(config):
        yield TestClient(app)


class _FakeDiscovery:
    def __init__(self, result: ACPModelDiscovery) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings, **kwargs: Any) -> ACPModelDiscovery:
        self.calls.append({"settings": settings, **kwargs})
        return self.result


@pytest.fixture
def fake_discovery(monkeypatch: pytest.MonkeyPatch) -> _FakeDiscovery:
    fake = _FakeDiscovery(
        ACPModelDiscovery(
            current_model_id="m1", available_models=[ACPModelInfo(model_id="m1")]
        )
    )
    monkeypatch.setattr(acp_router_module, "discover_acp_models", fake)
    return fake


def _discover(client: TestClient, **body: Any):
    return client.post("/api/acp/models", json={"agent_settings": FAKE_AGENT, **body})


def test_reports_a_live_servers_models(
    client: TestClient, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAKE_ACP_KEY", raising=False)

    response = _discover(client)

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["agent_name"] == "fake-acp"
    assert body["current_model_id"] == "m1"
    assert [m["model_id"] for m in body["available_models"]] == ["m1", "m2"]
    assert list(config.conversations_path.iterdir()) == []


def test_resolves_stored_secrets_by_reference(
    client: TestClient, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAKE_ACP_KEY", raising=False)
    get_secrets_store(config).set_secret("FAKE_ACP_KEY", "stored-key")
    lookup = {"kind": "LookupSecret", "url": "/api/settings/secrets/FAKE_ACP_KEY"}

    response = _discover(client, secrets={"FAKE_ACP_KEY": lookup})

    models = [m["model_id"] for m in response.json()["available_models"]]
    assert models == ["m1", "m2", "premium"]


def test_reuses_a_successful_result(
    client: TestClient, fake_discovery: _FakeDiscovery
) -> None:
    first = _discover(client)
    second = _discover(client)

    assert first.json() == second.json()
    assert len(fake_discovery.calls) == 1


def test_refresh_asks_the_server_again(
    client: TestClient, fake_discovery: _FakeDiscovery
) -> None:
    _discover(client)
    _discover(client, refresh=True)

    assert len(fake_discovery.calls) == 2


def test_new_credentials_ask_the_server_again(
    client: TestClient, fake_discovery: _FakeDiscovery
) -> None:
    def static(value: str) -> dict[str, str]:
        return {"kind": "StaticSecret", "value": value}

    _discover(client, secrets={"KEY": static("a")})
    _discover(client, secrets={"KEY": static("a")})
    _discover(client, secrets={"KEY": static("b")})

    assert [call["secrets"] for call in fake_discovery.calls] == [
        {"KEY": "a"},
        {"KEY": "b"},
    ]


def test_does_not_reuse_a_failure(
    client: TestClient, fake_discovery: _FakeDiscovery
) -> None:
    fake_discovery.result = ACPModelDiscovery(
        error=ACPModelDiscoveryError(code="ACPAuthRequired", detail="log in")
    )

    first = _discover(client)
    _discover(client)

    assert first.status_code == 200
    assert first.json()["error"] == {"code": "ACPAuthRequired", "detail": "log in"}
    assert len(fake_discovery.calls) == 2


def test_uses_the_stored_codex_login_like_a_conversation(
    client: TestClient, config: Config, fake_discovery: _FakeDiscovery
) -> None:
    get_secrets_store(config).set_secret(CODEX_AUTH_SECRET_NAME, '{"tokens": {}}')
    sent = {"kind": "StaticSecret", "value": "pasted"}

    client.post(
        "/api/acp/models",
        json={
            "agent_settings": {"agent_kind": "acp", "acp_server": "codex"},
            "secrets": {CODEX_AUTH_SECRET_NAME: sent},
        },
    )

    (call,) = fake_discovery.calls
    assert CODEX_AUTH_SECRET_NAME not in call["secrets"]
    binding = call["credential_bindings"][CODEX_AUTH_SECRET_NAME]
    assert isinstance(binding, LocalVersionedCredentialBinding)


def test_rejects_a_custom_server_without_a_command(client: TestClient) -> None:
    response = client.post(
        "/api/acp/models",
        json={"agent_settings": {"agent_kind": "acp", "acp_server": "custom"}},
    )

    assert response.status_code == 422


def test_is_unavailable_when_conversations_run_in_containers(
    client: TestClient, config: Config, fake_discovery: _FakeDiscovery
) -> None:
    app = client.app
    assert isinstance(app, FastAPI)
    app.state.config = config.model_copy(update={"conversation_runtime": "docker"})

    response = _discover(client)

    assert response.status_code == 501
    assert fake_discovery.calls == []
