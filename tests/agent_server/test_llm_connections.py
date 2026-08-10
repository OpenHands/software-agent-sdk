"""Tests for the Provider Connection endpoints (OpenHands/OpenHands#15492)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server import llm_connections as conn_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    FileConnectionsStore,
    PersistedConnections,
    ProviderConnection,
    reset_stores,
)


@pytest.fixture
def temp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "profiles").mkdir(parents=True, exist_ok=True)
        yield base


@pytest.fixture
def client(temp_dirs, monkeypatch):
    reset_stores()
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_dirs))
    config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
    # Patch the connections store to the temp dir (mirrors profiles_router tests).
    with patch(
        "openhands.agent_server.llm_connections.get_connections_store",
        lambda *_a, **_kw: FileConnectionsStore(persistence_dir=temp_dirs),
    ):
        app = create_app(config)
        yield TestClient(app)
    reset_stores()


def test_list_empty(client):
    r = client.get("/api/llm/connections")
    assert r.status_code == 200
    assert r.json() == []


def test_create_then_list(client):
    r = client.post(
        "/api/llm/connections",
        json={"provider": "openai", "key": "sk-test", "models": ["gpt-4o"]},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["provider"] == "openai"
    assert body["models"] == ["gpt-4o"]
    assert body["api_key_set"] is True
    # Key never echoed.
    assert "key" not in body
    assert "secret_name" not in body
    cid = body["id"]

    r = client.get("/api/llm/connections")
    assert r.status_code == 200
    listed = r.json()
    assert len(listed) == 1
    assert listed[0]["id"] == cid


def test_create_unknown_provider_422(client):
    r = client.post(
        "/api/llm/connections",
        json={"provider": "nope_provider", "key": "k"},
    )
    assert r.status_code == 422


def test_get_connection(client):
    cid = client.post(
        "/api/llm/connections", json={"provider": "anthropic", "key": "sk-ant"}
    ).json()["id"]
    r = client.get(f"/api/llm/connections/{cid}")
    assert r.status_code == 200
    assert r.json()["provider"] == "anthropic"
    assert r.json()["api_key_set"] is True


def test_get_missing_404(client):
    r = client.get("/api/llm/connections/does-not-exist")
    assert r.status_code == 404


def test_patch_rotate_label_models(client):
    cid = client.post(
        "/api/llm/connections", json={"provider": "openai", "key": "sk-1"}
    ).json()["id"]

    r = client.patch(
        f"/api/llm/connections/{cid}",
        json={"label": "work", "models": ["gpt-4o", "gpt-4o-mini"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "work"
    assert body["models"] == ["gpt-4o", "gpt-4o-mini"]

    # Rotate key: api_key_set stays true.
    r = client.patch(f"/api/llm/connections/{cid}", json={"key": "sk-2"})
    assert r.status_code == 200
    assert r.json()["api_key_set"] is True


def test_patch_requires_a_field(client):
    cid = client.post(
        "/api/llm/connections", json={"provider": "openai", "key": "sk-1"}
    ).json()["id"]
    r = client.patch(f"/api/llm/connections/{cid}", json={})
    assert r.status_code == 422


def test_patch_missing_connection_404(client):
    r = client.patch(
        "/api/llm/connections/none", json={"label": "x"}
    )
    # When only label/models are set (no key), the 404 comes from patch().
    assert r.status_code == 404


def test_delete_removes_connection_and_secret(client):
    cid = client.post(
        "/api/llm/connections", json={"provider": "openai", "key": "sk-1"}
    ).json()["id"]
    r = client.delete(f"/api/llm/connections/{cid}")
    assert r.status_code == 204
    assert client.get(f"/api/llm/connections/{cid}").status_code == 404
    # Listing is empty again.
    assert client.get("/api/llm/connections").json() == []


def test_delete_missing_404(client):
    assert client.delete("/api/llm/connections/none").status_code == 404


def test_validate_success_stamps_timestamp(client):
    cid = client.post(
        "/api/llm/connections",
        json={"provider": "openai", "key": "sk-test"},
    ).json()["id"]
    r = client.post(f"/api/llm/connections/{cid}/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert len(body["models"]) > 0

    # last_validated_at got stamped.
    conn = client.get(f"/api/llm/connections/{cid}").json()
    assert conn["last_validated_at"] is not None


def test_validate_missing_connection_404(client):
    assert (
        client.post("/api/llm/connections/none/validate").status_code == 404
    )


def test_validate_uses_injected_validator(client, monkeypatch):
    """validate_provider_key is module-level so tests can monkeypatch it."""

    def fake(provider, key):
        return True, ["fake-model-a", "fake-model-b"], None

    monkeypatch.setattr(conn_module, "validate_provider_key", fake)
    cid = client.post(
        "/api/llm/connections", json={"provider": "openai", "key": "sk"}
    ).json()["id"]
    body = client.post(f"/api/llm/connections/{cid}/validate").json()
    assert body["ok"] is True
    assert body["models"] == ["fake-model-a", "fake-model-b"]


def test_create_limit_enforced(client, monkeypatch):
    monkeypatch.setattr(conn_module, "MAX_CONNECTIONS", 2)
    for i in range(2):
        assert (
            client.post(
                "/api/llm/connections",
                json={"provider": "openai", "key": f"sk-{i}"},
            ).status_code
            == 201
        )
    r = client.post(
        "/api/llm/connections", json={"provider": "openai", "key": "sk-3"}
    )
    assert r.status_code == 409


# ── secret-by-name resolution at the LLM layer ────────────────────────────


def test_llm_secret_ref_helpers_roundtrip():
    from openhands.sdk.llm.llm import (
        LLM_SECRET_REF_PREFIX,
        llm_secret_ref,
        parse_llm_secret_ref,
    )

    ref = llm_secret_ref("llm_connection_abc")
    assert ref == f"{LLM_SECRET_REF_PREFIX}llm_connection_abc"
    assert parse_llm_secret_ref(ref) == "llm_connection_abc"
    # Raw key (no prefix) is not a reference.
    assert parse_llm_secret_ref("sk-raw-key") is None
    assert parse_llm_secret_ref(None) is None


def test_llm_resolves_secret_ref_via_resolver():
    from openhands.sdk.llm.llm import LLM, register_llm_secret_resolver

    register_llm_secret_resolver(lambda name: f"resolved-{name}" if name else None)
    try:
        llm = LLM(model="gpt-4o", api_key=SecretStr("secret:llm_connection_abc"))
        assert llm._get_api_key_value() == "resolved-llm_connection_abc"
    finally:
        register_llm_secret_resolver(None)


def test_llm_secret_ref_without_resolver_is_none():
    from openhands.sdk.llm.llm import LLM, register_llm_secret_resolver

    register_llm_secret_resolver(None)
    llm = LLM(model="gpt-4o", api_key=SecretStr("secret:llm_connection_abc"))
    assert llm._get_api_key_value() is None


def test_llm_raw_key_unaffected_by_resolver():
    from openhands.sdk.llm.llm import LLM, register_llm_secret_resolver

    register_llm_secret_resolver(lambda name: "should-not-be-used")
    try:
        llm = LLM(model="gpt-4o", api_key=SecretStr("sk-raw-key"))
        assert llm._get_api_key_value() == "sk-raw-key"
    finally:
        register_llm_secret_resolver(None)


# ── persistence layer ─────────────────────────────────────────────────────


def test_connections_store_roundtrip(temp_dirs):
    store = FileConnectionsStore(persistence_dir=temp_dirs)
    assert store.load() is None

    conn = ProviderConnection(
        id="abc",
        provider="openai",
        label="work",
        secret_name="llm_connection_abc",
        models=["gpt-4o"],
        created_at=1700000000,
    )
    persisted = store.update(lambda c: PersistedConnections(connections=[conn]))
    assert persisted.connections[0].id == "abc"

    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.connections[0].secret_name == "llm_connection_abc"
    assert reloaded.schema_version == 1


def test_persisted_connections_schema_version_guard():
    # Newer schema versions are rejected to avoid silent data loss.
    with pytest.raises(ValueError):
        PersistedConnections.from_persisted({"schema_version": 99, "connections": []})
