import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.docker_runtime.routers import (
    delete_conversation,
    docker_conversation_router,
    proxy_conversation,
)
from openhands.agent_server.event_router import event_read_router
from openhands.agent_server.models import UpdateSecretsRequest
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import LookupSecret


def test_docker_mode_replaces_local_conversation_execution_routes(tmp_path):
    app = create_app(
        Config(
            conversation_runtime="docker",
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
        )
    )
    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/api/conversations" in paths
    assert "/sockets/events/{conversation_id}" in paths
    assert "/sockets/session/{conversation_id}" in paths
    assert "/sockets/bash-events" in paths
    assert "/api/conversations/{conversation_id}/{tail:path}" in paths
    assert "/api/conversations/{conversation_id}/events/search" in paths
    assert "/api/conversations/{conversation_id}" in paths
    assert "/api/host/bash/execute_bash_command" not in paths
    assert "/api/bash/execute_bash_command" in paths

    scope = {
        "type": "http",
        "path": f"/api/conversations/{uuid4()}",
        "root_path": "",
        "method": "PATCH",
    }
    matched = [
        route
        for route in app.routes
        if hasattr(route, "matches") and route.matches(scope)[0] is Match.FULL
    ]
    assert getattr(matched[0], "endpoint").__name__ == "proxy_conversation_root"

    scope["method"] = "GET"
    matched = [
        route
        for route in app.routes
        if hasattr(route, "matches") and route.matches(scope)[0] is Match.FULL
    ]
    assert getattr(matched[0], "endpoint").__name__ == "get_conversation"

    event_scope = {
        "type": "http",
        "path": f"/api/conversations/{uuid4()}/events/search",
        "root_path": "",
        "method": "GET",
    }
    matched = [
        route
        for route in app.routes
        if hasattr(route, "matches") and route.matches(event_scope)[0] is Match.FULL
    ]
    assert getattr(matched[0], "endpoint").__name__ == "search_conversation_events"

    session_scope = {
        "type": "websocket",
        "path": f"/sockets/session/{uuid4()}",
        "root_path": "",
    }
    matched = [
        route
        for route in app.routes
        if hasattr(route, "matches") and route.matches(session_scope)[0] is Match.FULL
    ]
    assert getattr(matched[0], "endpoint").__name__ == "proxy_session"

    # Static collection paths must reach their real handlers before the
    # Docker ``/{conversation_id}`` catch-all tries to parse them as UUIDs.
    client = TestClient(app)
    for path in ("/api/conversations/search", "/api/conversations/count"):
        assert client.get(path).status_code != 422


def test_root_conversation_proxy_preserves_canonical_path(tmp_path, monkeypatch):
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    app = FastAPI()
    app.state.conversation_registry = DockerConversationRegistry(config)
    app.state.conversation_service = AsyncMock()
    app.include_router(docker_conversation_router, prefix="/api")
    conversation_id = uuid4()
    captured = {}

    async def container(*_args):
        return SimpleNamespace(host="http://inner", api_key="inner-key")

    async def proxy(*_args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.routers._container", container
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.routers.proxy_http", proxy
    )

    with TestClient(app) as client:
        response = client.patch(
            f"/api/conversations/{conversation_id}", json={"title": "Updated"}
        )

    assert response.status_code == 200
    assert captured["upstream_path"] == f"/api/conversations/{conversation_id}"


def test_runtime_credentials_and_release_use_the_existing_sdk_contract(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    identity = RuntimeProvisioningStore(config).create(conversation_id)
    conversation_dir = config.conversations_path / conversation_id.hex
    conversation_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    stopped = []

    async def stop(conversation_id):
        stopped.append(conversation_id)

    app = FastAPI()
    registry = DockerConversationRegistry(config)
    registry.stop = stop
    app.state.conversation_registry = registry
    app.state.conversation_service = AsyncMock()
    app.include_router(docker_conversation_router, prefix="/api")
    with TestClient(app) as client:
        response = client.post(
            f"/api/conversations/{conversation_id}/runtime/credentials"
        )
        assert response.json() == {
            "session_api_key": identity.api_key.get_secret_value()
        }
        assert (
            client.delete(f"/api/conversations/{conversation_id}/runtime").status_code
            == 204
        )
    assert stopped == [conversation_id]
    app.state.conversation_service.refresh_persisted_conversation.assert_awaited_once_with(
        conversation_id
    )


def test_runtime_info_marks_legacy_local_conversation_non_resumable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    registry = DockerConversationRegistry(config)
    legacy_id = uuid4()
    legacy_dir = registry.conversation_dir(legacy_id)
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "meta.json").write_text("{}")

    docker_id = uuid4()
    registry.provisioning.create(docker_id)
    docker_dir = registry.conversation_dir(docker_id)
    docker_dir.mkdir(parents=True)
    (docker_dir / "meta.json").write_text("{}")

    app = FastAPI()
    app.state.conversation_registry = registry
    app.include_router(docker_conversation_router, prefix="/api")
    with TestClient(app) as client:
        legacy = client.get(f"/api/conversations/{legacy_id}/runtime")
        docker = client.get(f"/api/conversations/{docker_id}/runtime")

    assert legacy.status_code == 200
    assert legacy.json() == {
        "runtime_status": "missing",
        "can_resume": False,
        "runtime_error": None,
    }
    assert docker.status_code == 200
    assert docker.json() == {
        "runtime_status": "missing",
        "can_resume": True,
        "runtime_error": None,
    }


def test_docker_event_history_reads_persistence_without_starting_a_container(
    tmp_path, monkeypatch
):
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    registry = DockerConversationRegistry(config)
    registry.provisioning.create(conversation_id)
    registry.get_or_create = AsyncMock()
    event_service = SimpleNamespace(
        search_events=AsyncMock(return_value={"items": [], "next_page_id": None})
    )
    app = FastAPI()
    app.state.conversation_registry = registry
    app.state.conversation_service = SimpleNamespace(
        get_persisted_event_service=AsyncMock(return_value=event_service),
        get_event_service=AsyncMock(),
    )
    app.include_router(event_read_router, prefix="/api")

    with TestClient(app) as client:
        response = client.get(
            f"/api/conversations/{conversation_id}/events/search?limit=50"
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_page_id": None}
    app.state.conversation_service.get_persisted_event_service.assert_awaited_once_with(
        conversation_id
    )
    app.state.conversation_service.get_event_service.assert_not_awaited()
    registry.get_or_create.assert_not_awaited()


def test_delete_stops_runtime_before_removing_outer_owned_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    registry = DockerConversationRegistry(config)
    registry.provisioning.create(conversation_id)
    conversation_dir = registry.conversation_dir(conversation_id)
    conversation_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    runtime_dir = registry.provisioning.runtime_dir(conversation_id)
    (runtime_dir / "persistence").mkdir()
    registry.stop = AsyncMock()

    app = FastAPI()
    app.state.conversation_registry = registry
    app.state.conversation_service = AsyncMock()
    app.include_router(docker_conversation_router, prefix="/api")
    with TestClient(app) as client:
        response = client.delete(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    registry.stop.assert_awaited_once_with(conversation_id)
    app.state.conversation_service.refresh_persisted_conversation.assert_awaited_once_with(
        conversation_id
    )
    assert not conversation_dir.exists()
    assert not runtime_dir.exists()


@pytest.mark.asyncio
async def test_delete_blocks_runtime_restart_while_container_stops(tmp_path):
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    registry = DockerConversationRegistry(config)
    registry.provisioning.create(conversation_id)
    conversation_dir = registry.conversation_dir(conversation_id)
    conversation_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    stop_started = asyncio.Event()
    allow_stop = asyncio.Event()

    async def stop(conversation_id):
        stop_started.set()
        await allow_stop.wait()

    registry.stop = stop
    request = Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": f"/api/conversations/{conversation_id}",
            "query_string": b"",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    conversation_registry=registry,
                    conversation_service=AsyncMock(),
                )
            ),
        }
    )

    deletion = asyncio.create_task(delete_conversation(conversation_id, request))
    await stop_started.wait()
    with pytest.raises(RuntimeError, match="Conversation is being deleted"):
        await registry.get_or_create(conversation_id)
    allow_stop.set()

    response = await deletion
    assert response.status_code == 200
    assert not registry.provisioning.manifest_path(conversation_id).exists()


@pytest.mark.asyncio
async def test_secret_updates_are_materialized_and_profile_scoped(
    tmp_path, monkeypatch
):
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    registry = DockerConversationRegistry(config)
    conversation_id = uuid4()
    identity = registry.provisioning.create(conversation_id).model_copy(
        update={
            "launched_agent_profile": LaunchedAgentProfile(
                agent_profile_id=uuid4(), revision=1, secret_refs=["ALLOWED"]
            )
        }
    )
    registry.provisioning.save(identity)
    looked_up = []

    def get_value(secret):
        looked_up.append(secret.url)
        return f"resolved-{secret.url.rsplit('/', 1)[-1]}"

    monkeypatch.setattr(LookupSecret, "get_value", get_value)
    captured = {}

    async def container(*_args):
        return SimpleNamespace(host="http://inner", api_key="inner-key")

    async def proxy(*_args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.routers._container", container
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.routers.proxy_http", proxy
    )
    payload = {
        "secrets": {
            name: LookupSecret(
                url=f"http://outer/api/settings/secrets/{name}"
            ).model_dump(mode="json", context={"expose_secrets": True})
            for name in ("ALLOWED", "DENIED")
        }
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode(),
            "more_body": False,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/conversations/{conversation_id}/secrets",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    conversation_registry=registry,
                    conversation_service=AsyncMock(),
                )
            ),
        },
        receive,
    )
    await proxy_conversation(conversation_id, "secrets", request)

    forwarded = UpdateSecretsRequest.model_validate_json(captured["body"])
    assert set(forwarded.secrets) == {"ALLOWED"}
    assert forwarded.secrets["ALLOWED"].get_value() == "resolved-ALLOWED"
    assert looked_up == ["http://outer/api/settings/secrets/ALLOWED"]
    request.app.state.conversation_service.refresh_persisted_conversation.assert_awaited_once_with(
        conversation_id
    )
