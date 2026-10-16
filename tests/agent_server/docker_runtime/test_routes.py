import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
)
from openhands.agent_server.docker_runtime.routers import (
    delete_conversation,
    docker_conversation_router,
    proxy_conversation,
)
from openhands.agent_server.event_router import event_read_router
from openhands.agent_server.models import (
    ConversationInfo,
    StoredConversation,
    UpdateSecretsRequest,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import LookupSecret
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.utils import utc_now
from openhands.sdk.workspace import LocalWorkspace


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


def test_runtime_release_prunes_cache_but_keeps_conversation_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    registry = DockerConversationRegistry(config)
    conversation_id = uuid4()
    registry.provisioning.create(conversation_id)
    conversation_dir = registry.conversation_dir(conversation_id)
    conversation_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    (conversation_dir / "base_state.json").write_text(
        json.dumps({"execution_status": "finished"})
    )
    runtime_dir = registry.provisioning.runtime_dir(conversation_id)
    cache = runtime_dir / "persistence" / ".cache" / "uv"
    cache.mkdir(parents=True)
    (runtime_dir / "workspace" / "main.py").write_text("print('hi')")
    stopped = []
    monkeypatch.setattr(
        ConversationContainer, "stop", lambda self: stopped.append(self.container_id)
    )
    registry._containers[conversation_id] = ConversationContainer(
        "http://inner", "inner-key", "inner-container"
    )

    app = FastAPI()
    app.state.conversation_registry = registry
    app.state.conversation_service = AsyncMock()
    app.include_router(docker_conversation_router, prefix="/api")
    with TestClient(app) as client:
        response = client.delete(f"/api/conversations/{conversation_id}/runtime")

    assert response.status_code == 204
    assert stopped == ["inner-container"]
    assert not (runtime_dir / "persistence" / ".cache").exists()
    assert (runtime_dir / "workspace" / "main.py").read_text() == "print('hi')"
    assert (conversation_dir / "meta.json").is_file()
    assert registry.provisioning.manifest_path(conversation_id).is_file()


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


def test_archive_stops_runtime_and_unarchive_stays_cold(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    registry = DockerConversationRegistry(config)
    registry.provisioning.create(conversation_id)
    transitions = []
    registry.stop = AsyncMock(side_effect=lambda _: transitions.append("stop"))
    archived_at = utc_now()
    conversation = ConversationInfo(
        id=conversation_id,
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test-model"), tools=[]),
    )
    service = AsyncMock()

    async def set_archived(_, *, archived):
        transitions.append("archive" if archived else "unarchive")
        return (
            conversation.model_copy(update={"archived_at": archived_at})
            if archived
            else conversation
        )

    service.set_conversation_archived.side_effect = set_archived

    app = FastAPI()
    app.state.conversation_registry = registry
    app.state.conversation_service = service
    app.include_router(docker_conversation_router, prefix="/api")
    with TestClient(app) as client:
        archived = client.post(f"/api/conversations/{conversation_id}/archive")
        unarchived = client.post(f"/api/conversations/{conversation_id}/unarchive")

    assert archived.status_code == 200
    assert archived.json()["archived_at"] == archived_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert unarchived.status_code == 200
    assert unarchived.json()["archived_at"] is None
    registry.stop.assert_awaited_once_with(conversation_id)
    service.set_conversation_archived.assert_has_awaits(
        [
            call(conversation_id, archived=True),
            call(conversation_id, archived=False),
        ]
    )
    assert transitions == ["stop", "archive", "unarchive"]


@pytest.mark.asyncio
async def test_archive_keeps_metadata_written_by_stopping_runtime(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    registry = DockerConversationRegistry(config)
    registry.provisioning.create(conversation_id)
    directory = registry.conversation_dir(conversation_id)
    directory.mkdir(parents=True)
    stored = StoredConversation(
        id=conversation_id,
        workspace=LocalWorkspace(working_dir="/workspace"),
        confirmation_policy=NeverConfirm(),
    )
    (directory / "meta.json").write_text(stored.model_dump_json())
    (directory / "base_state.json").write_text(
        ConversationState(
            id=conversation_id,
            agent=Agent(llm=LLM(model="test-model"), tools=[]),
            workspace=stored.workspace,
            confirmation_policy=stored.confirmation_policy,
        ).model_dump_json()
    )

    async def inner_runtime_shutdown(_):
        runtime_stored = stored.model_copy(update={"title": "Title set by runtime"})
        (directory / "meta.json").write_text(runtime_stored.model_dump_json())

    registry.stop = AsyncMock(side_effect=inner_runtime_shutdown)

    app = FastAPI()
    app.state.conversation_registry = registry
    app.include_router(docker_conversation_router, prefix="/api")
    async with ConversationService(
        conversations_dir=config.conversations_path
    ) as service:
        app.state.conversation_service = service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/conversations/{conversation_id}/archive"
            )

    assert response.status_code == 200
    assert response.json()["title"] == "Title set by runtime"
    persisted = json.loads((directory / "meta.json").read_text())
    assert persisted["title"] == "Title set by runtime"
    assert persisted["archived_at"] is not None


def test_archived_conversation_cannot_start_a_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer-key"),
    )
    conversation_id = uuid4()
    registry = DockerConversationRegistry(config)
    registry.provisioning.create(conversation_id)
    directory = registry.conversation_dir(conversation_id)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text('{"archived_at":"2026-08-14T00:00:00+00:00"}')

    app = FastAPI()
    app.state.conversation_registry = registry
    app.state.conversation_service = AsyncMock()
    app.include_router(docker_conversation_router, prefix="/api")
    with TestClient(app) as client:
        start = client.post(
            "/api/conversations", json={"conversation_id": str(conversation_id)}
        )
        reprovision = client.post(
            f"/api/conversations/{conversation_id}/runtime/reprovision"
        )
        proxy = client.get(f"/api/conversations/{conversation_id}/run")

    assert start.status_code == 409
    assert reprovision.status_code == 409
    assert proxy.status_code == 409
    assert registry.get(conversation_id) is None


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


def _docker_start_app(
    tmp_path, monkeypatch
) -> tuple[FastAPI, DockerConversationRegistry]:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer-key"),
    )
    registry = DockerConversationRegistry(config)
    app = FastAPI()
    app.state.conversation_registry = registry
    app.state.conversation_service = AsyncMock()
    app.include_router(docker_conversation_router, prefix="/api")
    return app, registry


@pytest.mark.parametrize("existing", [False, True])
def test_a_rejected_start_stops_only_a_container_it_created(
    tmp_path, monkeypatch, existing
):
    app, registry = _docker_start_app(tmp_path, monkeypatch)
    conversation_id = uuid4()
    if existing:
        registry.provisioning.create(conversation_id)
        conversation_dir = registry.conversation_dir(conversation_id)
        conversation_dir.mkdir(parents=True, exist_ok=True)
        (conversation_dir / "meta.json").write_text("{}")
    stopped = []

    async def get_or_create(_conversation_id):
        return SimpleNamespace(host="http://inner", api_key="inner-key")

    async def stop(stopped_id):
        stopped.append(stopped_id)

    async def post(self, url, **kwargs):
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(registry, "get_or_create", get_or_create)
    monkeypatch.setattr(registry, "stop", stop)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    with TestClient(app) as client:
        response = client.post(
            "/api/conversations",
            json={
                "conversation_id": str(conversation_id),
                "agent": {"kind": "Agent", "llm": {"model": "test"}},
            },
        )

    assert response.status_code == 500
    assert stopped == ([] if existing else [conversation_id])


def test_a_symlinked_conversation_dir_is_rejected(tmp_path, monkeypatch):
    app, registry = _docker_start_app(tmp_path, monkeypatch)
    conversation_id = uuid4()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    registry.config.conversations_path.mkdir(parents=True, exist_ok=True)
    (registry.config.conversations_path / conversation_id.hex).symlink_to(elsewhere)

    with TestClient(app) as client:
        response = client.post(
            "/api/conversations",
            json={
                "conversation_id": str(conversation_id),
                "agent": {"kind": "Agent", "llm": {"model": "test"}},
            },
        )

    assert response.status_code == 422
