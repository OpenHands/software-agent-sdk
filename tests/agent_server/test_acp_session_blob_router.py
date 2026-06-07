"""Tests for the ACP session-blob export/import routes."""

import io
import tarfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


@pytest.fixture
def conversations_path(tmp_path):
    return tmp_path / "conversations"


@pytest.fixture
def client(monkeypatch, conversations_path):
    monkeypatch.setattr(
        "openhands.agent_server.acp_session_blob_router.get_default_config",
        lambda: SimpleNamespace(conversations_path=conversations_path),
    )
    config = Config(session_api_keys=[])  # Disable authentication
    return TestClient(create_app(config), raise_server_exceptions=False)


def _seed_codex_session(conversations_path, conversation_id) -> None:
    root = conversations_path / conversation_id.hex / "acp" / "codex"
    sessions = root / "sessions" / "2026" / "06" / "07"
    sessions.mkdir(parents=True)
    (sessions / "rollout-abc.jsonl").write_text('{"turn": 1}\n')
    (root / "auth.json").write_text('{"refresh_token": "SECRET"}')


def _make_blob(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_export_returns_allowlisted_tarball(client, conversations_path):
    conversation_id = uuid4()
    _seed_codex_session(conversations_path, conversation_id)

    response = client.get(f"/api/acp_session_blob/{conversation_id}/codex")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
        assert tar.getnames() == ["sessions/2026/06/07/rollout-abc.jsonl"]


def test_export_204_when_no_session_files(client):
    response = client.get(f"/api/acp_session_blob/{uuid4()}/codex")
    assert response.status_code == 204


def test_export_unknown_provider_404(client):
    response = client.get(f"/api/acp_session_blob/{uuid4()}/not-a-provider")
    assert response.status_code == 404


def test_export_snapshotless_provider_422(client):
    response = client.get(f"/api/acp_session_blob/{uuid4()}/gemini-cli")
    assert response.status_code == 422


def test_import_seeds_fresh_conversation_dir(client, conversations_path):
    conversation_id = uuid4()
    blob = _make_blob({"sessions/2026/06/07/rollout-abc.jsonl": b'{"turn": 1}\n'})

    response = client.put(
        f"/api/acp_session_blob/{conversation_id}/codex",
        content=blob,
        headers={"Content-Type": "application/gzip"},
    )

    assert response.status_code == 200
    assert response.json() == {"files_written": 1}
    restored = (
        conversations_path
        / conversation_id.hex
        / "acp"
        / "codex"
        / "sessions"
        / "2026"
        / "06"
        / "07"
        / "rollout-abc.jsonl"
    )
    assert restored.read_text() == '{"turn": 1}\n'


def test_import_never_clobbers_live_files(client, conversations_path):
    conversation_id = uuid4()
    _seed_codex_session(conversations_path, conversation_id)
    blob = _make_blob({"sessions/2026/06/07/rollout-abc.jsonl": b"STALE"})

    response = client.put(
        f"/api/acp_session_blob/{conversation_id}/codex", content=blob
    )

    assert response.status_code == 200
    assert response.json() == {"files_written": 0}
    live = (
        conversations_path
        / conversation_id.hex
        / "acp"
        / "codex"
        / "sessions"
        / "2026"
        / "06"
        / "07"
        / "rollout-abc.jsonl"
    )
    assert live.read_text() == '{"turn": 1}\n'


def test_import_rejects_traversal_blob(client, conversations_path):
    conversation_id = uuid4()
    blob = _make_blob({"sessions/../auth.json": b'{"refresh_token": "EVIL"}'})

    response = client.put(
        f"/api/acp_session_blob/{conversation_id}/codex", content=blob
    )

    assert response.status_code == 422
    root = conversations_path / conversation_id.hex / "acp" / "codex"
    assert not (root / "auth.json").exists()


def test_import_rejects_empty_body(client):
    response = client.put(f"/api/acp_session_blob/{uuid4()}/codex", content=b"")
    assert response.status_code == 400


def test_import_rejects_garbage(client):
    response = client.put(
        f"/api/acp_session_blob/{uuid4()}/codex", content=b"not a tarball"
    )
    assert response.status_code == 422


def test_routes_require_session_api_key(monkeypatch, conversations_path):
    monkeypatch.setattr(
        "openhands.agent_server.acp_session_blob_router.get_default_config",
        lambda: SimpleNamespace(conversations_path=conversations_path),
    )
    config = Config(session_api_keys=["test-key"])
    client = TestClient(create_app(config), raise_server_exceptions=False)
    conversation_id = uuid4()

    assert (
        client.get(f"/api/acp_session_blob/{conversation_id}/codex").status_code == 401
    )
    response = client.get(
        f"/api/acp_session_blob/{conversation_id}/codex",
        headers={"X-Session-API-Key": "test-key"},
    )
    assert response.status_code in (200, 204)
