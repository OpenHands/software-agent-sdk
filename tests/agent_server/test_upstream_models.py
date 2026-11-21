"""Tests for upstream model catalog discovery and non-blocking profile drift checks."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server import upstream_models
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import (
    get_llm_profile_store,
    get_provider_connections_store,
)
from openhands.agent_server.persistence import ProviderConnectionStore
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnection


class _StandInServer:
    """Local HTTP stand-in for an OpenAI-compatible upstream ``/v1/models`` server."""

    def __init__(
        self,
        handler: Callable[[BaseHTTPRequestHandler], None],
    ) -> None:
        self.received_paths: list[str] = []
        self.received_auth_headers: list[str | None] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.received_paths.append(self.path)
                outer.received_auth_headers.append(self.headers.get("Authorization"))
                handler(self)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> _StandInServer:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@pytest.fixture
def env(tmp_path):
    """TestClient wired with isolated profile and provider-connection stores."""
    provider_store = ProviderConnectionStore(base_dir=tmp_path / "provider-connections")
    profile_store = LLMProfileStore(base_dir=tmp_path / "profiles")
    config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
    app = create_app(config)
    app.dependency_overrides[get_llm_profile_store] = lambda: profile_store
    app.dependency_overrides[get_provider_connections_store] = lambda: provider_store
    return TestClient(app), profile_store, provider_store


def test_upstream_models_openai_data_shape_anonymous(env):
    """GET /api/llm/models/upstream parses OpenAI data[].id without credentials."""
    client, _, _ = env

    def handle(req: BaseHTTPRequestHandler) -> None:
        _write_json(
            req,
            200,
            {
                "object": "list",
                "data": [
                    {"id": "custom-model-b", "object": "model"},
                    {"id": "custom-model-a", "object": "model"},
                    {"id": "custom-model-a", "object": "model"},
                ],
            },
        )

    with _StandInServer(handle) as server:
        response = client.get(
            "/api/llm/models/upstream", params={"base_url": server.base_url}
        )

    assert response.status_code == 200
    assert response.json() == {"models": ["custom-model-a", "custom-model-b"]}
    assert server.received_paths == ["/v1/models"]
    assert server.received_auth_headers == [None]


def test_upstream_models_flat_models_shape_and_v1_normalization(env):
    """Parses flat models[] with id/slug/name keys and avoids /v1/v1 duplication."""
    client, _, _ = env

    def handle(req: BaseHTTPRequestHandler) -> None:
        _write_json(
            req,
            200,
            {
                "models": [
                    {"slug": "codex-alpha", "display_name": "Codex Alpha"},
                    {"name": "router-beta"},
                    {"id": "gateway-gamma"},
                ]
            },
        )

    with _StandInServer(handle) as server:
        response = client.get(
            "/api/llm/models/upstream",
            params={"base_url": f"{server.base_url}/v1/"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "models": ["codex-alpha", "gateway-gamma", "router-beta"]
    }
    assert server.received_paths == ["/v1/models"]


def test_upstream_models_uses_profile_api_key_server_side(env):
    """Profile API key is sent server-side and never echoed in response or errors."""
    client, profile_store, _ = env
    secret_key = "sk-super-secret-profile-token-12345"

    def handle(req: BaseHTTPRequestHandler) -> None:
        if req.headers.get("Authorization") != f"Bearer {secret_key}":
            _write_json(req, 401, {"error": "unauthorized"})
            return
        _write_json(req, 200, {"data": [{"id": "private-model-1"}]})

    with _StandInServer(handle) as server:
        profile_store.save(
            "private-proxy",
            LLM(
                model="openai/private-model-1",
                base_url=server.base_url,
                api_key=secret_key,
            ),
            include_secrets=True,
        )
        response = client.get(
            "/api/llm/models/upstream", params={"profile_name": "private-proxy"}
        )

    assert response.status_code == 200
    assert response.json() == {"models": ["private-model-1"]}
    assert secret_key not in response.text
    assert server.received_auth_headers == [f"Bearer {secret_key}"]


def test_upstream_models_uses_linked_provider_connection_key(env):
    """Profile linked to a provider connection uses the connection's API key."""
    client, profile_store, provider_store = env
    conn_key = "sk-provider-connection-secret-98765"

    def handle(req: BaseHTTPRequestHandler) -> None:
        if req.headers.get("Authorization") != f"Bearer {conn_key}":
            _write_json(req, 401, {"error": "unauthorized"})
            return
        _write_json(req, 200, {"data": [{"id": "linked-model"}]})

    with _StandInServer(handle) as server:
        provider_store.create(
            ProviderConnection(
                id="corp-gw",
                display_name="Corp Gateway",
                base_url=server.base_url,
                api_key=SecretStr(conn_key),
                created_at=1,
                updated_at=1,
            )
        )
        profile_store.save(
            "linked-profile",
            LLM(
                model="openai/linked-model",
                provider_connection_id="corp-gw",
            ),
            include_secrets=True,
        )
        response = client.get(
            "/api/llm/models/upstream", params={"profile_name": "linked-profile"}
        )

    assert response.status_code == 200
    assert response.json() == {"models": ["linked-model"]}
    assert conn_key not in response.text
    assert server.received_auth_headers == [f"Bearer {conn_key}"]


def test_upstream_models_does_not_leak_profile_key_to_different_base_url(env):
    """Never send a profile's API key when caller overrides base_url to another host."""
    client, profile_store, _ = env
    secret_key = "sk-do-not-exfiltrate-55555"

    def handle(req: BaseHTTPRequestHandler) -> None:
        _write_json(req, 200, {"data": [{"id": "public-model"}]})

    with _StandInServer(handle) as other_server:
        profile_store.save(
            "secret-profile",
            LLM(
                model="openai/private",
                base_url="https://trusted.example.com",
                api_key=secret_key,
            ),
            include_secrets=True,
        )
        response = client.get(
            "/api/llm/models/upstream",
            params={
                "profile_name": "secret-profile",
                "base_url": other_server.base_url,
            },
        )

    assert response.status_code == 200
    assert other_server.received_auth_headers == [None]


def test_upstream_models_error_cases(env, monkeypatch):
    """Non-2xx, malformed JSON, timeout, and bad input return typed 4xx/5xx errors."""
    client, _, _ = env

    # Missing both params -> 400
    res_missing = client.get("/api/llm/models/upstream")
    assert res_missing.status_code == 400

    # Invalid URL scheme -> 400
    res_scheme = client.get(
        "/api/llm/models/upstream", params={"base_url": "file:///etc/passwd"}
    )
    assert res_scheme.status_code == 400

    # Non-2xx status -> 502
    with _StandInServer(
        lambda req: _write_json(req, 503, {"error": "backend down"})
    ) as server:
        res_503 = client.get(
            "/api/llm/models/upstream", params={"base_url": server.base_url}
        )
    assert res_503.status_code == 502

    # Unparseable JSON body -> 502
    def bad_json(req: BaseHTTPRequestHandler) -> None:
        raw = b"<html>not json</html>"
        req.send_response(200)
        req.send_header("Content-Type", "text/html")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    with _StandInServer(bad_json) as server:
        res_bad = client.get(
            "/api/llm/models/upstream", params={"base_url": server.base_url}
        )
    assert res_bad.status_code == 502

    # Timeout -> 504
    monkeypatch.setattr(upstream_models, "UPSTREAM_MODELS_TIMEOUT_SECONDS", 0.15)

    def slow(req: BaseHTTPRequestHandler) -> None:
        time.sleep(0.5)
        _write_json(req, 200, {"data": []})

    with _StandInServer(slow) as server:
        res_timeout = client.get(
            "/api/llm/models/upstream", params={"base_url": server.base_url}
        )
    assert res_timeout.status_code == 504


def test_validate_profile_optional_catalog_drift_warning(env):
    """POST /api/profiles/{name}/validate reports catalog mismatch as warning only."""
    client, _, _ = env

    with (
        _StandInServer(
            lambda req: _write_json(req, 200, {"data": [{"id": "served-model"}]})
        ) as server,
        patch("openhands.sdk.llm.llm.LLM.uses_responses_api", return_value=False),
        patch("openhands.sdk.llm.llm.LLM.acompletion", return_value=MagicMock()),
    ):
        # Mismatched model -> valid=True with non-blocking warning
        res_drift = client.post(
            "/api/profiles/drift-test/validate",
            json={
                "llm": {
                    "model": "openai/stale-model",
                    "base_url": server.base_url,
                    "api_key": "sk-test",
                },
                "check_upstream_catalog": True,
            },
        )
        assert res_drift.status_code == 200
        body = res_drift.json()
        assert body["valid"] is True
        assert body["error"] is None
        assert len(body["warnings"]) == 1
        assert "stale-model" in body["warnings"][0]

        # Matching model (unprefixed) -> valid=True with no warnings
        res_match = client.post(
            "/api/profiles/match-test/validate",
            json={
                "llm": {
                    "model": "openai/served-model",
                    "base_url": server.base_url,
                    "api_key": "sk-test",
                },
                "check_upstream_catalog": True,
            },
        )
        assert res_match.status_code == 200
        assert res_match.json()["valid"] is True
        assert not res_match.json().get("warnings")


def test_validate_profile_failed_catalog_fetch_does_not_fail_validation(env):
    """A failing upstream catalog fetch never fails profile validation."""
    client, _, _ = env

    with (
        _StandInServer(
            lambda req: _write_json(req, 500, {"error": "catalog broken"})
        ) as server,
        patch("openhands.sdk.llm.llm.LLM.uses_responses_api", return_value=False),
        patch("openhands.sdk.llm.llm.LLM.acompletion", return_value=MagicMock()),
    ):
        response = client.post(
            "/api/profiles/catalog-down/validate",
            json={
                "llm": {
                    "model": "openai/any-model",
                    "base_url": server.base_url,
                    "api_key": "sk-test",
                },
                "check_upstream_catalog": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["error"] is None
    assert not body.get("warnings")
