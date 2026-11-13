"""Tests for the /conversations/search-content endpoint and service wiring."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config, TelemetrySpec
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.search_service import ContentSearchService
from tests.search.conftest import write_conversation


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
def client_factory(conv_root, tmp_path, monkeypatch):
    """App factory for configs that differ from the default ``client`` fixture."""

    def _make(*, content_search_enabled: bool = True) -> TestClient:
        monkeypatch.setenv("OH_SESSION_API_KEYS_0", "test-key")
        config = Config(
            session_api_keys=["test-key"],
            conversations_path=conv_root,
            workspace_path=tmp_path / "workspace",
            content_search_enabled=content_search_enabled,
        )
        return TestClient(create_app(config))

    return _make


@pytest.fixture
def client(conv_root, tmp_path, monkeypatch):
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "test-key")
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(conv_root))
    config = Config(
        session_api_keys=["test-key"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "workspace",
        content_search_enabled=True,
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
        content_search_enabled=True,
    )
    app = create_app(config)
    return TestClient(app)


def _assert_fallthrough_parity(resp, unknown):
    """Compare two catch-all responses structurally.

    Live routing turns both into a 422 UUID-parse error whose body echoes
    the path input, so bodies differ by input. Without a live conversation
    service (unit harness, no lifespan) both are 503. Either way the two
    paths must be indistinguishable: same status, same body shape.
    """
    assert resp.status_code == unknown.status_code
    assert type(resp.json()["detail"]) is type(unknown.json()["detail"])
    if isinstance(resp.json()["detail"], list):
        a, b = resp.json()["detail"][0], unknown.json()["detail"][0]
        assert a["type"] == b["type"] and a["loc"] == b["loc"]


def test_remote_deployment_hides_content_search(remote_client):
    """Hosted deployments must not serve content search at all."""
    assert not hasattr(remote_client.app.state, "content_search_service")
    # The route is not mounted, so the path falls through to the
    # ``/{conversation_id}`` catch-all exactly like any other unknown
    # ``/conversations/`` path: no fingerprint for the feature.
    resp = remote_client.get(
        "/api/conversations/search-content",
        params={"query": "garage"},
        headers={"X-Session-API-Key": "test-key"},
    )
    unknown = remote_client.get(
        "/api/conversations/definitely-not-a-route",
        headers={"X-Session-API-Key": "test-key"},
    )
    _assert_fallthrough_parity(resp, unknown)
    # And no OpenAPI schema entry may advertise it.
    assert (
        "/api/conversations/search-content" not in remote_client.app.openapi()["paths"]
    )


def test_remote_schema_has_no_search_entry(remote_client):
    """The OpenAPI schema must not advertise the endpoint on hosted deployments."""
    assert (
        "/api/conversations/search-content" not in remote_client.app.openapi()["paths"]
    )
    assert not hasattr(remote_client.app.state, "content_search_service")


def test_disabled_by_default_locally(client_factory):
    """Without an explicit opt-in, local deployments get no service or route."""
    app = client_factory(content_search_enabled=False)
    assert not hasattr(app.app.state, "content_search_service")
    assert "/api/conversations/search-content" not in app.app.openapi()["paths"]
    # Fallthrough parity: identical to any unknown /conversations/ path.
    resp = app.get(
        "/api/conversations/search-content",
        params={"query": "garage"},
        headers={"X-Session-API-Key": "test-key"},
    )
    unknown = app.get(
        "/api/conversations/definitely-not-a-route",
        headers={"X-Session-API-Key": "test-key"},
    )
    _assert_fallthrough_parity(resp, unknown)


def test_probe_enabled_when_service_exists(client):
    """The UI hides the search box on 404, so an enabled server must answer."""
    resp = client.get(
        "/api/conversations/search/enabled",
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}


def test_probe_absent_when_disabled(client_factory):
    """No route, no schema entry, no fingerprint when the flag is off."""
    app = client_factory(content_search_enabled=False)
    assert "/api/conversations/search/enabled" not in app.app.openapi()["paths"]
    probe = app.get(
        "/api/conversations/search/enabled",
        headers={"X-Session-API-Key": "test-key"},
    )
    # Parity against another nonexistent two-segment path: one-segment
    # unknowns hit the /{conversation_id} catch-all (503 without a live
    # service), while unmapped two-segment paths return a bare 404 —
    # the same answer upstream main gives for any route that never existed.
    unknown = app.get(
        "/api/conversations/definitely-not-a-route/definitely-not-a-route",
        headers={"X-Session-API-Key": "test-key"},
    )
    _assert_fallthrough_parity(probe, unknown)


def test_probe_absent_on_remote_deployment(remote_client):
    """Hosted deployments keep the upstream-identical schema: no probe route."""
    assert (
        "/api/conversations/search/enabled" not in remote_client.app.openapi()["paths"]
    )


def test_overlong_query_is_rejected(client):
    """Queries beyond the cap are rejected before hitting the index."""
    resp = client.get(
        "/api/conversations/search-content",
        params={"query": "garage " * 60},
        headers={"X-Session-API-Key": "test-key"},
    )
    assert resp.status_code == 422


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


def test_delete_conversation_prunes_search_index(conv_root, tmp_path, monkeypatch):
    """Deleting a conversation removes it from the search index immediately.

    The service's delete machinery is stubbed (covered by its own tests);
    the prune hook itself — service lookup, thread offload, index removal —
    runs against a real index.
    """
    import shutil

    conv_id = write_conversation(
        conv_root, "Prune me", [("user", "This one gets deleted soon.")]
    )
    config = Config(
        session_api_keys=["k"],
        conversations_path=conv_root,
        workspace_path=tmp_path / "w",
        content_search_enabled=True,
    )
    service = ContentSearchService(config)
    service.sync()
    hits, _ = service.search("deleted")
    assert conv_id in {h["conversation_id"] for h in hits}

    app = FastAPI()
    app.state.content_search_service = service
    app.include_router(conversation_router, prefix="/api")
    stub_service = MagicMock()

    # Every search re-syncs from disk, so the stub must actually remove the
    # directory — like the real service — or backfill would re-index it.
    def _delete(conv_id):
        shutil.rmtree(conv_root / conv_id.hex, ignore_errors=True)
        return True

    stub_service.delete_conversation = AsyncMock(side_effect=_delete)
    app.dependency_overrides[get_conversation_service] = lambda: stub_service
    with TestClient(app) as client:
        resp = client.delete(f"/api/conversations/{conv_id}")
        assert resp.status_code == 200, resp.text
    hits, _ = service.search("deleted")
    assert conv_id not in {h["conversation_id"] for h in hits}


def test_backfill_prunes_orphaned_conversations(conv_root, tmp_path):
    """Backfill drops index rows for conversations whose directory is gone."""
    import shutil

    from openhands.agent_server.search_index import SearchIndex, backfill

    index_path = conv_root.parent / "search-index.db"
    index = SearchIndex(index_path)
    stats = backfill(index, conv_root)
    assert stats["pruned_orphans"] == 0
    before = index.conversation_ids()
    assert len(before) == 2
    # Simulate a conversation deleted while the server was down.
    victim = sorted(conv_root.iterdir())[0]
    conv_id = victim.name
    shutil.rmtree(victim)
    stats = backfill(index, conv_root)
    assert stats["pruned_orphans"] == 1
    assert index.conversation_ids() == before - {conv_id}
