"""Real registry/filesystem reads; no Docker daemon or LLM is used here."""

import shutil

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import openhands.agent_server.server_details_router as details
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.server_context import server_context_of


@pytest.mark.parametrize("mode", ["local", "docker", "hybrid"])
def test_storage_status_is_read_only(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
        static_files_path=None,
        enable_vscode=False,
        secret_key=SecretStr("storage-fixture-key"),
        conversation_runtime="docker" if mode == "docker" else "local",
        conversation_runtime_selectable=mode == "hybrid",
        conversation_storage_disk_budget=0.8,
        conversation_storage_retention_days=7,
    )
    app = create_app(config)
    registry = server_context_of(app).conversation_registry
    before_activity = details._last_event_time
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    client = TestClient(app)
    response = client.get("/api/storage")
    assert response.status_code == 200
    if mode == "local":
        assert response.json() == {"managed_storage": None}
    else:
        reported = response.json()["managed_storage"]
        actual = shutil.disk_usage(registry.reclaimer.storage.root)
        assert reported["scope"] == "docker_runtime_filesystem"
        assert abs(reported["free_bytes"] - actual.free) < 64 * 1024 * 1024
        assert (
            abs(reported["usage_fraction"] - actual.used / (actual.used + actual.free))
            < 0.001
        )
        assert reported["disk_budget"] == 0.8
        assert reported["retention_days"] == 7
        assert "root" not in reported
    assert details._last_event_time == before_activity
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before
    schema = client.get("/openapi.json").json()
    assert "/api/storage" in schema["paths"]
    assert "StorageStatus" in schema["components"]["schemas"]


def test_storage_status_keeps_existing_api_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    client = TestClient(
        create_app(
            Config(
                conversations_path=tmp_path / "conversations",
                workspace_path=tmp_path / "workspace",
                static_files_path=None,
                enable_vscode=False,
                secret_key=SecretStr("storage-fixture-key"),
                session_api_keys=["storage-test-key"],
            )
        )
    )
    assert client.get("/api/storage").status_code == 401
    assert (
        client.get(
            "/api/storage", headers={"X-Session-API-Key": "storage-test-key"}
        ).status_code
        == 200
    )


def test_storage_status_is_unavailable_before_deferred_init(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    client = TestClient(
        create_app(
            Config(
                conversations_path=tmp_path / "conversations",
                workspace_path=tmp_path / "workspace",
                static_files_path=None,
                enable_vscode=False,
                deferred_init=True,
            )
        )
    )
    # The readiness dependency uses the lifespan-owned InitService.
    with client:
        assert client.get("/api/storage").status_code == 503


@pytest.mark.asyncio
async def test_storage_capacity_read_runs_off_event_loop(tmp_path, monkeypatch):
    import threading

    import openhands.agent_server.docker_runtime.registry as registry_module
    from openhands.agent_server.persistence import ServerStores

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
        secret_key=SecretStr("storage-fixture-key"),
    )
    registry = registry_module.DockerConversationRegistry(
        config, ServerStores.from_config(config)
    )
    loop_thread = threading.get_ident()
    observed = []
    real_read = registry_module.read_storage_usage

    def recording_read(*args):
        observed.append(threading.get_ident())
        return real_read(*args)

    monkeypatch.setattr(registry_module, "read_storage_usage", recording_read)
    result = await registry.storage_status()
    assert result.managed_storage is not None
    assert observed and loop_thread not in observed
