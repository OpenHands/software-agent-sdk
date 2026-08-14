"""Tests for the Model Provider endpoints (OpenHands/OpenHands#15492)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import llm_providers as prov_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    FileProvidersStore,
    get_secrets_store,
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
    with patch(
        "openhands.agent_server.llm_providers.get_providers_store",
        lambda *_a, **_kw: FileProvidersStore(persistence_dir=temp_dirs),
    ):
        app = create_app(config)
        yield TestClient(app)
    reset_stores()


def _create(client, **overrides):
    body = {
        "display_name": "OpenAI",
        "kind": "openai",
        "key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "wire_api": "chat",
        "custom_headers": {"X-Org": "eng"},
        "models": [{"name": "gpt-5.6-luna"}],
    }
    body.update(overrides)
    return client.post("/api/llm/model-providers", json=body)


def test_list_empty(client):
    r = client.get("/api/llm/model-providers")
    assert r.status_code == 200
    assert r.json() == []


def test_create_then_list(client):
    r = _create(client)
    assert r.status_code == 201
    body = r.json()
    assert body["display_name"] == "OpenAI"
    assert body["kind"] == "openai"
    assert body["base_url"] == "https://api.openai.com/v1"
    assert body["wire_api"] == "chat"
    assert body["custom_headers"] == {"X-Org": "eng"}
    assert body["models"] == [{"name": "gpt-5.6-luna", "wire_api": None}]
    assert body["api_key_set"] is True
    # Key/secret never echoed.
    assert "key" not in body
    assert "secret_name" not in body

    r2 = client.get("/api/llm/model-providers")
    assert r2.status_code == 200
    listed = r2.json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]
    assert listed[0]["api_key_set"] is True


def test_key_stored_as_named_secret(client, temp_dirs, monkeypatch):
    r = _create(client)
    pid = r.json()["id"]
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_dirs))
    reset_stores()
    store = get_secrets_store()
    assert store.get_secret(f"llm_provider_{pid}") == "sk-test"


def test_get_and_404(client):
    r = _create(client)
    pid = r.json()["id"]
    assert client.get(f"/api/llm/model-providers/{pid}").status_code == 200
    assert client.get("/api/llm/model-providers/nope").status_code == 404


def test_update_fields_and_rotate_key(client, temp_dirs, monkeypatch):
    pid = _create(client).json()["id"]
    r = client.patch(
        f"/api/llm/model-providers/{pid}",
        json={
            "display_name": "OpenAI Prod",
            "key": "sk-rotated",
            "wire_api": "responses",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "OpenAI Prod"
    assert body["wire_api"] == "responses"

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_dirs))
    reset_stores()
    assert get_secrets_store().get_secret(f"llm_provider_{pid}") == "sk-rotated"


def test_update_requires_a_field(client):
    pid = _create(client).json()["id"]
    r = client.patch(f"/api/llm/model-providers/{pid}", json={})
    assert r.status_code == 422


def test_delete_removes_provider_and_secret(client, temp_dirs, monkeypatch):
    pid = _create(client).json()["id"]
    r = client.delete(f"/api/llm/model-providers/{pid}")
    assert r.status_code == 200
    assert r.json()["api_key_set"] is False
    assert client.get(f"/api/llm/model-providers/{pid}").status_code == 404

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_dirs))
    reset_stores()
    assert get_secrets_store().get_secret(f"llm_provider_{pid}") is None


def test_add_edit_remove_model(client):
    pid = _create(client).json()["id"]

    # Add
    r = client.post(
        f"/api/llm/model-providers/{pid}/models",
        json={"name": "gpt-5.6-sol", "wire_api": "responses"},
    )
    assert r.status_code == 201
    names = [m["name"] for m in r.json()["models"]]
    assert names == ["gpt-5.6-luna", "gpt-5.6-sol"]

    # Duplicate add -> 409
    dup = client.post(
        f"/api/llm/model-providers/{pid}/models", json={"name": "gpt-5.6-sol"}
    )
    assert dup.status_code == 409

    # Edit (rename + change wire api)
    r = client.patch(
        f"/api/llm/model-providers/{pid}/models/gpt-5.6-sol",
        json={"name": "gpt-5.6-terra", "wire_api": "chat"},
    )
    assert r.status_code == 200
    models = {m["name"]: m["wire_api"] for m in r.json()["models"]}
    assert models == {"gpt-5.6-luna": None, "gpt-5.6-terra": "chat"}

    # Remove
    r = client.delete(f"/api/llm/model-providers/{pid}/models/gpt-5.6-luna")
    assert r.status_code == 200
    assert [m["name"] for m in r.json()["models"]] == ["gpt-5.6-terra"]

    # Remove missing -> 404
    assert (
        client.delete(f"/api/llm/model-providers/{pid}/models/nope").status_code == 404
    )


def test_test_probe_never_mutates_models(client, monkeypatch):
    pid = _create(client).json()["id"]

    monkeypatch.setattr(prov_module, "_live_probe", lambda *a, **k: (True, None))
    r = client.post(f"/api/llm/model-providers/{pid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["verified"] is True
    assert isinstance(body["suggested_models"], list)

    # The provider's curated model list is unchanged.
    after = client.get(f"/api/llm/model-providers/{pid}").json()
    assert [m["name"] for m in after["models"]] == ["gpt-5.6-luna"]


def test_test_probe_reports_bad_key(client, monkeypatch):
    pid = _create(client).json()["id"]
    monkeypatch.setattr(
        prov_module, "_live_probe", lambda *a, **k: (False, "401 invalid key")
    )
    r = client.post(f"/api/llm/model-providers/{pid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["verified"] is False
    assert body["suggested_models"] == []
    assert "401" in body["error"]


def test_custom_endpoint_test_offers_catalog_without_probe(client):
    # A kind litellm doesn't recognize (a custom OpenAI-compatible endpoint):
    # ``test`` can't probe it, so it returns ok=True but verified=False.
    pid = _create(client, kind="my-vllm", base_url="http://localhost:1234/v1").json()[
        "id"
    ]
    r = client.post(f"/api/llm/model-providers/{pid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["verified"] is False
