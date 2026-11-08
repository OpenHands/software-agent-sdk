"""Tests for the /conversations/search-content endpoint and service wiring."""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.search_index import SearchIndex, backfill
from openhands.agent_server.search_service import ContentSearchService


def _write_conversation(root: Path, title: str, messages: list) -> str:
    conv_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, title))
    conv_dir = root / conv_id
    events_dir = conv_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "meta.json").write_text(
        json.dumps({"conversation_id": conv_id, "title": title})
    )
    for i, (source, text) in enumerate(messages):
        event = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{title}:{i}")),
            "timestamp": "2026-11-01T10:00:00",
            "source": source,
            "llm_message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        (events_dir / f"event-{i:06d}-{event['id']}.json").write_text(
            json.dumps(event)
        )
    return conv_id


@pytest.fixture
def conv_root(tmp_path):
    convs = tmp_path / "conversations"
    convs.mkdir()
    _write_conversation(
        convs,
        "Garage door sensor",
        [
            ("user", "The garage door sensor keeps triggering false alarms."),
            ("agent", "False alarms happen below five degrees at night."),
        ],
    )
    _write_conversation(
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
        file_store_path=tmp_path / "filestore",
    )
    app = create_app(config)
    return TestClient(app)


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
    assert "false alarms" in hit["snippet"]
    assert hit["conversation_id"]


def test_endpoint_requires_query_param(client):
    resp = client.get(
        "/api/conversations/search-content",
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 422


def test_endpoint_requires_auth(client):
    resp = client.get(
        "/api/conversations/search-content", params={"query": "garage"}
    )
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
        file_store_path=tmp_path / "f",
    )
    svc = ContentSearchService(config)
    hits = svc.search("Rome")
    assert hits and hits[0]["title"] == "Italy trip"
    # index landed next to conversations dir, as designed
    assert (conv_root.parent / "search-index.db").exists()


def test_service_search_empty_query_returns_nothing(conv_root, tmp_path):
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
        file_store_path=tmp_path / "f",
    )
    svc = ContentSearchService(config)
    assert svc.search("   ") == []


def test_new_message_found_after_incremental_sync(conv_root, tmp_path):
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
        file_store_path=tmp_path / "f",
    )
    svc = ContentSearchService(config)
    assert svc.search("kubernetes") == []
    _write_conversation(
        conv_root, "K8s migration", [("user", "Plan the kubernetes migration.")]
    )
    hits = svc.search("kubernetes")
    assert hits and hits[0]["title"] == "K8s migration"
