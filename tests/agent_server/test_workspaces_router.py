"""Tests for workspaces_router endpoints.

Workspaces persisted on the agent-server (workspace/.openhands/workspaces.json)
replace the previous browser-local Zustand store, so every client connected to
the same server sees the same list. These tests cover the HTTP surface the
GUI consumes plus the file-locked persistence underneath it.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        reset_stores()
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(Path(tmpdir) / "persist"))
        config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
        app = create_app(config)
        yield TestClient(app)
        reset_stores()


def test_list_workspaces_empty_when_no_file(client):
    # Act
    response = client.get("/api/workspaces")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"workspaces": [], "workspaceParents": []}


def test_add_workspaces_persists_camelcase_parent_path_and_dedupes_by_path(client):
    # Arrange
    payload = {
        "workspaces": [
            {"id": "/a", "name": "a", "path": "/a"},
            {
                "id": "/b",
                "name": "b",
                "path": "/b",
                "parentPath": "/parents/root",
            },
        ]
    }

    # Act: first add seeds the list; second add with an overlapping path is a no-op
    first = client.post("/api/workspaces", json=payload)
    second = client.post(
        "/api/workspaces",
        json={"workspaces": [{"id": "/a", "name": "a", "path": "/a"}]},
    )
    listed = client.get("/api/workspaces")

    # Assert
    assert first.status_code == 200
    assert second.status_code == 200
    assert listed.status_code == 200
    body = listed.json()
    assert [w["path"] for w in body["workspaces"]] == ["/a", "/b"]
    # camelCase wire format preserved — TS LocalWorkspace shape is unchanged.
    assert body["workspaces"][1]["parentPath"] == "/parents/root"


def test_delete_workspace_returns_404_when_path_absent(client):
    # Arrange
    client.post(
        "/api/workspaces",
        json={"workspaces": [{"id": "/a", "name": "a", "path": "/a"}]},
    )

    # Act
    removed_present = client.delete("/api/workspaces", params={"path": "/a"})
    removed_missing = client.delete("/api/workspaces", params={"path": "/nope"})
    remaining = client.get("/api/workspaces").json()

    # Assert
    assert removed_present.status_code == 200
    assert removed_present.json() == {"deleted": True}
    assert removed_missing.status_code == 404
    assert remaining["workspaces"] == []


def test_workspace_parents_add_and_remove_independent_of_workspaces(client):
    # Arrange: seed one workspace and one parent so we can prove they don't
    # collide when mutated independently.
    client.post(
        "/api/workspaces",
        json={"workspaces": [{"id": "/w", "name": "w", "path": "/w"}]},
    )

    # Act
    added = client.post(
        "/api/workspaces/parents",
        json={"parents": [{"id": "/p", "name": "p", "path": "/p"}]},
    )
    after_add = client.get("/api/workspaces").json()
    removed = client.delete("/api/workspaces/parents", params={"path": "/p"})
    missing = client.delete("/api/workspaces/parents", params={"path": "/p"})
    after_remove = client.get("/api/workspaces").json()

    # Assert
    assert added.status_code == 200
    assert [p["path"] for p in after_add["workspaceParents"]] == ["/p"]
    assert [w["path"] for w in after_add["workspaces"]] == ["/w"]
    assert removed.status_code == 200
    assert missing.status_code == 404
    assert after_remove["workspaceParents"] == []
    # Workspace survived the parent's removal.
    assert [w["path"] for w in after_remove["workspaces"]] == ["/w"]


def test_workspaces_survive_across_requests_via_disk_persistence(client):
    # Arrange: write something
    import os

    client.post(
        "/api/workspaces",
        json={"workspaces": [{"id": "/keep", "name": "keep", "path": "/keep"}]},
    )

    # Act: confirm it's on disk, then reset the in-memory singleton (simulating
    # a server restart) and re-read.
    persist_dir = Path(os.environ["OH_PERSISTENCE_DIR"])
    assert (persist_dir / "workspaces.json").exists()

    reset_stores()
    listed_again = client.get("/api/workspaces").json()

    # Assert
    assert [w["path"] for w in listed_again["workspaces"]] == ["/keep"]
