"""Tests for the /conversations/search-content endpoint and service wiring."""

import pytest
from conftest import write_conversation
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config, TelemetrySpec
from openhands.agent_server.search_service import ContentSearchService


@pytest.fixture
def conv_root(tmp_path):
    convs = tmp_path / "conversations"
    convs.mkdir()
    write_conversation(
        convs,
        "Garage door sensor",
        [
            ("user", "The garage door sensor keeps triggering false alarms."),
            ("agent", "False alarms happen below five degrees at night."),
        ],
    )
    write_conversation(
        convs, "Italy trip", [("user", "Ten day itinerary starting in Rome.")]
    )
    return convs


@pytest.fixture
def client(conv_root, tmp_path, monkeypatch):
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "test-key")
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(conv_root))
    config = Config(
        session_api_keys=["test-key"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "workspace",
    )
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def remote_client(conv_root, tmp_path, monkeypatch):
    """App configured as a hosted deployment: no content search service."""
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "test-key")
    config = Config(
        session_api_keys=["test-key"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "workspace",
        telemetry=TelemetrySpec(deployment_kind="remote"),
    )
    app = create_app(config)
    return TestClient(app)


def test_remote_deployment_hides_content_search(remote_client):
    """Hosted deployments must not serve content search at all."""
    assert not hasattr(remote_client.app.state, "content_search_service")
    resp = remote_client.get(
        "/api/conversations/search-content",
        params={"query": "garage"},
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 404


def test_endpoint_returns_snippets(client):
    resp = client.get(
        "/api/conversations/search-content",
        params={"query": "garage false alarms"},
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["capped"] is False
    hit = body["results"][0]
    assert hit["title"] == "Garage door sensor"
    # Snippets wrap matched words in [ ] for UI emphasis; multi-word
    # queries mark each matched word.
    assert "triggering [false] [alarms]" in hit["snippet"]
    assert hit["conversation_id"]


def test_endpoint_requires_query_param(client):
    resp = client.get(
        "/api/conversations/search-content",
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 422


def test_endpoint_requires_auth(client):
    resp = client.get("/api/conversations/search-content", params={"query": "garage"})
    assert resp.status_code in (401, 403)


def test_endpoint_empty_query_is_422(client):
    # min_length=1 on the query param rejects empty strings at validation time
    resp = client.get(
        "/api/conversations/search-content",
        params={"query": ""},
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 422


def test_endpoint_hostile_fts_syntax_is_safe(client):
    resp = client.get(
        "/api/conversations/search-content",
        params={"query": '"garage (door"'},
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_service_search_direct(conv_root, tmp_path):
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
    )
    svc = ContentSearchService(config)
    hits, capped = svc.search("Rome")
    assert hits and hits[0]["title"] == "Italy trip"
    assert capped is False
    # index landed next to conversations dir, as designed
    assert (conv_root.parent / "search-index.db").exists()


def test_service_search_empty_query_returns_nothing(conv_root, tmp_path):
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
    )
    svc = ContentSearchService(config)
    assert svc.search("   ") == ([], False)


def test_new_message_found_after_incremental_sync(conv_root, tmp_path):
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
    )
    svc = ContentSearchService(config)
    assert svc.search("kubernetes")[0] == []
    write_conversation(
        conv_root, "K8s migration", [("user", "Plan the kubernetes migration.")]
    )
    hits, _ = svc.search("kubernetes")
    assert hits and hits[0]["title"] == "K8s migration"
