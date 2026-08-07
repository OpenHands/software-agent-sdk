"""Tests for meta_profiles_router endpoints."""

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import get_settings_store, reset_stores
from openhands.sdk.llm.meta_profile_store import MetaProfile, MetaProfileStore
from openhands.sdk.settings.model import OpenHandsAgentSettings


@pytest.fixture
def temp_settings_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_dir = Path(tmpdir) / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        yield settings_dir


@pytest.fixture
def temp_meta_profiles_dir(temp_settings_dir):
    # The router resolves meta-profiles at ``OH_PERSISTENCE_DIR/meta-profiles``,
    # and the client fixture sets OH_PERSISTENCE_DIR to temp_settings_dir, so the
    # store fixture must seed the same dir the router reads.
    meta_dir = temp_settings_dir / "meta-profiles"
    meta_dir.mkdir(parents=True, exist_ok=True)
    yield meta_dir


@pytest.fixture
def client(temp_meta_profiles_dir, temp_settings_dir, monkeypatch):
    reset_stores()
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))
    config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
    app = create_app(config)
    # No MetaProfileStore patch needed: the router now honors OH_PERSISTENCE_DIR
    # and resolves the same dir the store fixture seeds.
    yield TestClient(app)
    reset_stores()


@pytest.fixture
def store(temp_meta_profiles_dir):
    return MetaProfileStore(base_dir=temp_meta_profiles_dir)


def _meta(classifier="minimax", default="gpt", classes=None) -> dict:
    return MetaProfile.model_validate(
        {
            "classifier_model": classifier,
            "default_model": default,
            "classes": classes or [{"description": "UI", "model": "deepseek"}],
        }
    ).model_dump(mode="json")


# ── Persistence-dir resolution (regression for #3835) ────────────────────────


def test_meta_profile_store_honors_persistence_dir(monkeypatch, tmp_path):
    """The router resolves meta-profiles under OH_PERSISTENCE_DIR, not the host
    ~/.openhands/meta-profiles, so isolated agent-servers stay isolated."""
    from openhands.agent_server.meta_profiles_router import _get_meta_profile_store

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    store = _get_meta_profile_store()
    assert store.base_dir == tmp_path / "meta-profiles"


def test_meta_profile_store_falls_back_to_home(monkeypatch, tmp_path):
    """Without OH_PERSISTENCE_DIR, fall back to ~/.openhands/meta-profiles so
    meta-profiles stay co-located with the LLM profiles they reference by name
    (not a workspace-relative dir)."""
    from openhands.agent_server.meta_profiles_router import _get_meta_profile_store

    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    store = _get_meta_profile_store()
    assert store.base_dir == fake_home / ".openhands" / "meta-profiles"


# ── List ────────────────────────────────────────────────────────────────────


def test_list_empty(client):
    response = client.get("/api/meta-profiles")
    assert response.status_code == 200
    body = response.json()
    assert body["meta_profiles"] == []
    assert body["active_meta_profile"] is None


def test_list_returns_summaries(client, store):
    store.save("balanced", MetaProfile.model_validate(_meta()))
    response = client.get("/api/meta-profiles")
    assert response.status_code == 200
    body = response.json()
    assert body["meta_profiles"] == [
        {
            "name": "balanced",
            "classifier_model": "minimax",
            "default_model": "gpt",
            "num_classes": 1,
        }
    ]


# ── Get ──────────────────────────────────────────────────────────────────────


def test_get_meta_profile(client, store):
    store.save("balanced", MetaProfile.model_validate(_meta()))
    response = client.get("/api/meta-profiles/balanced")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "balanced"
    assert body["config"]["classifier_model"] == "minimax"
    assert body["config"]["classes"][0]["model"] == "deepseek"


def test_get_missing_returns_404(client):
    response = client.get("/api/meta-profiles/nope")
    assert response.status_code == 404


# ── Save ─────────────────────────────────────────────────────────────────────


def test_save_creates_meta_profile(client, store):
    response = client.post("/api/meta-profiles/balanced", json=_meta())
    assert response.status_code == 201
    assert store.load("balanced").classifier_model == "minimax"


def test_save_overwrites(client, store):
    client.post("/api/meta-profiles/p", json=_meta(classifier="a", default="b"))
    response = client.post(
        "/api/meta-profiles/p", json=_meta(classifier="c", default="d")
    )
    assert response.status_code == 201
    assert store.load("p").classifier_model == "c"


def test_save_invalid_body_returns_422(client):
    response = client.post("/api/meta-profiles/p", json={"default_model": "gpt"})
    assert response.status_code == 422


def test_save_invalid_name_returns_422(client):
    response = client.post("/api/meta-profiles/..bad..", json=_meta())
    assert response.status_code == 422


def test_save_timeout_returns_503(client, monkeypatch):
    """Save surfaces store lock TimeoutError as a retryable 503, not a 500."""

    def boom(self, name, meta_profile, *, max_profiles=None):
        raise TimeoutError("locked")

    monkeypatch.setattr(MetaProfileStore, "save", boom)

    response = client.post("/api/meta-profiles/anything", json=_meta())
    assert response.status_code == 503


# ── Delete ───────────────────────────────────────────────────────────────────


def test_delete_meta_profile(client, store):
    store.save("p", MetaProfile.model_validate(_meta()))
    response = client.delete("/api/meta-profiles/p")
    assert response.status_code == 200
    assert store.list() == []


def test_delete_is_idempotent(client):
    response = client.delete("/api/meta-profiles/nope")
    assert response.status_code == 200


def test_delete_timeout_returns_503(client, monkeypatch):
    """Delete surfaces store lock TimeoutError as a retryable 503, not a 500."""

    def boom(self, name):
        raise TimeoutError("locked")

    monkeypatch.setattr(MetaProfileStore, "delete", boom)

    response = client.delete("/api/meta-profiles/anything")
    assert response.status_code == 503


def test_delete_active_clears_active(client, store):
    store.save("p", MetaProfile.model_validate(_meta()))
    activate = client.post("/api/meta-profiles/p/activate")
    assert activate.status_code == 200

    client.delete("/api/meta-profiles/p")

    listed = client.get("/api/meta-profiles").json()
    assert listed["active_meta_profile"] is None


def test_delete_active_clears_nested_agent_settings(client, store):
    """Deleting the active meta-profile must clear the *nested* agent settings.

    Otherwise the routing tool stays enabled pointing at a now-deleted profile,
    which breaks the next conversation. Guards against top-level/nested drift.
    """
    store.save("p", MetaProfile.model_validate(_meta()))
    client.post("/api/meta-profiles/p/activate")

    client.delete("/api/meta-profiles/p")

    persisted = get_settings_store().load()
    assert persisted is not None
    agent = persisted.agent_settings
    assert isinstance(agent, OpenHandsAgentSettings)
    assert agent.active_meta_profile is None
    assert agent.enable_classify_and_switch_llm_tool is False


# ── Activate ─────────────────────────────────────────────────────────────────


def test_activate_sets_active_meta_profile(client, store):
    store.save("p", MetaProfile.model_validate(_meta()))
    response = client.post("/api/meta-profiles/p/activate")
    assert response.status_code == 200
    assert response.json()["name"] == "p"

    listed = client.get("/api/meta-profiles").json()
    assert listed["active_meta_profile"] == "p"


def test_activate_propagates_into_agent_settings(client, store):
    """Activation must wire the *nested* agent settings, not just the facade.

    ``enable_classify_and_switch_llm_tool`` / ``active_meta_profile`` on
    ``agent_settings`` are what actually attach the routing tool; if only the
    top-level field is set, activation reports success but does nothing.
    """
    store.save("p", MetaProfile.model_validate(_meta()))
    response = client.post("/api/meta-profiles/p/activate")
    assert response.status_code == 200

    persisted = get_settings_store().load()
    assert persisted is not None
    assert persisted.active_meta_profile == "p"
    agent = persisted.agent_settings
    assert isinstance(agent, OpenHandsAgentSettings)
    assert agent.active_meta_profile == "p"
    assert agent.enable_classify_and_switch_llm_tool is True


def test_activate_missing_returns_404(client):
    response = client.post("/api/meta-profiles/nope/activate")
    assert response.status_code == 404


def test_concurrent_delete_and_activate_keep_settings_consistent(client, store):
    """Racing delete(active) against activate(other) must not corrupt settings.

    ``delete`` clears the active meta-profile and ``activate`` sets a new one;
    both route through the file-locked settings store. Whatever the interleaving,
    the persisted state must stay consistent: the active profile is never the
    just-deleted one, and the nested ``agent_settings`` never drifts from the
    top-level ``active_meta_profile``.
    """
    store.save("q", MetaProfile.model_validate(_meta()))

    for _ in range(15):
        # Re-establish "p" as the active profile before each race.
        store.save("p", MetaProfile.model_validate(_meta()))
        client.post("/api/meta-profiles/p/activate")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(client.delete, "/api/meta-profiles/p"),
                pool.submit(client.post, "/api/meta-profiles/q/activate"),
            ]
            for future in futures:
                assert future.result().status_code == 200

        persisted = get_settings_store().load()
        assert persisted is not None
        active = persisted.active_meta_profile
        # "p" was deleted, so it must never remain active; outcome is "q" or None
        # depending on which write committed last.
        assert active in (None, "q")

        agent = persisted.agent_settings
        assert isinstance(agent, OpenHandsAgentSettings)
        # Nested agent settings must agree with the top-level facade.
        assert agent.active_meta_profile == active
        assert agent.enable_classify_and_switch_llm_tool is (active is not None)
