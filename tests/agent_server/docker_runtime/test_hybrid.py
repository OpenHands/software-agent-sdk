import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.hybrid import (
    HybridConversationRegistry,
    RuntimeDispatchMiddleware,
)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return Config(
        conversation_runtime_selectable=True,
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer-key"),
    )


def test_selectable_mode_serves_both_runtimes(config):
    app = create_app(config)
    assert isinstance(app.state.conversation_registry, HybridConversationRegistry)
    paths = [getattr(route, "path", "") for route in app.routes]
    assert any(
        p.startswith("/api/conversations/{runtime_conversation_id}/") for p in paths
    )
    assert "/api/conversations/{conversation_id}/events" in paths
    assert "/api/_docker/conversations/{conversation_id}/{tail:path}" in paths
    assert "/api/_docker/conversations" in paths
    assert "/sockets/events/{conversation_id}" in paths
    assert "/_docker/sockets/events/{conversation_id}" in paths

    info = TestClient(app).get("/server_info").json()
    assert info["available_conversation_runtimes"] == ["local", "docker"]
    assert "selectable_conversation_runtime_v1" in info["capabilities"]


async def _dispatch(registry, scope, body=b""):
    seen = {}

    async def app(scope, receive, send):
        seen["path"] = scope["path"]
        message = await receive()
        seen["body"] = message.get("body", b"")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "root_path": "",
        "app": SimpleNamespace(state=SimpleNamespace(conversation_registry=registry)),
        **scope,
    }
    await RuntimeDispatchMiddleware(app)(scope, receive, None)  # type: ignore[arg-type]
    return seen


@pytest.mark.asyncio
async def test_dispatch_routes_conversations_by_runtime(config):
    registry = HybridConversationRegistry(config)
    docker_id, local_id = uuid4(), uuid4()
    registry.provisioning.create(docker_id)

    def http(method, path):
        return {"type": "http", "method": method, "path": path}

    seen = await _dispatch(
        registry, http("POST", f"/api/conversations/{docker_id}/events")
    )
    assert seen["path"] == f"/api/_docker/conversations/{docker_id}/events"
    seen = await _dispatch(
        registry, http("POST", f"/api/conversations/{local_id}/events")
    )
    assert seen["path"] == f"/api/conversations/{local_id}/events"
    # The bare GET is the shared catalog record.
    seen = await _dispatch(registry, http("GET", f"/api/conversations/{docker_id}"))
    assert seen["path"] == f"/api/conversations/{docker_id}"
    seen = await _dispatch(registry, http("DELETE", f"/api/conversations/{docker_id}"))
    assert seen["path"] == f"/api/_docker/conversations/{docker_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("default", "requested", "expected"),
    [
        ("local", None, "/api/conversations"),
        ("local", "docker", "/api/_docker/conversations"),
        ("docker", None, "/api/_docker/conversations"),
        ("docker", "local", "/api/conversations"),
    ],
)
async def test_dispatch_start_uses_requested_runtime(
    config, default, requested, expected
):
    registry = HybridConversationRegistry(
        config.model_copy(update={"conversation_runtime": default})
    )
    body = json.dumps(
        {"conversation_runtime": requested} if requested else {"x": 1}
    ).encode()
    seen = await _dispatch(
        registry, {"type": "http", "method": "POST", "path": "/api/conversations"}, body
    )
    assert seen == {"path": expected, "body": body}


@pytest.mark.asyncio
async def test_dispatch_routes_docker_sockets(config):
    registry = HybridConversationRegistry(config)
    docker_id = uuid4()
    registry.provisioning.create(docker_id)
    seen = await _dispatch(
        registry, {"type": "websocket", "path": f"/sockets/events/{docker_id}"}
    )
    assert seen["path"] == f"/_docker/sockets/events/{docker_id}"
