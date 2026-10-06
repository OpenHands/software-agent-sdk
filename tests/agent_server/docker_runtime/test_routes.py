import asyncio
import json
import threading
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from litellm.types.utils import ModelResponse
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import AutoTitleSubscriber
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.docker_runtime.routers import (
    delete_conversation,
    docker_conversation_router,
    proxy_conversation,
    start_conversation,
)
from openhands.agent_server.event_router import event_read_router
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation, UpdateSecretsRequest
from openhands.agent_server.persistence import get_llm_profile_store
from openhands.agent_server.persistence.store import get_provider_connections_store
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnection
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import LookupSecret
from openhands.sdk.security.confirmation_policy import NeverConfirm
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


@pytest.fixture
def title_start(tmp_path, monkeypatch):
    app, registry = _docker_start_app(tmp_path, monkeypatch)
    host = get_llm_profile_store()
    for name in ("title-aux", "other"):
        host.save(
            name,
            LLM(model=f"openai/{name}", api_key=SecretStr(f"{name}-key")),
            include_secrets=True,
            cipher=registry.config.cipher,
        )
    attempts = SimpleNamespace(fail=False, forwarded={})

    async def get_or_create(_conversation_id):
        registry.provisioning.load(_conversation_id)
        return SimpleNamespace(host="http://inner", api_key="inner-key")

    async def post(self, url, **kwargs):
        body = kwargs["json"]
        conversation_id = UUID(body["conversation_id"])
        if attempts.fail:
            return httpx.Response(500, request=httpx.Request("POST", url))
        attempts.forwarded.setdefault(conversation_id, body)
        directory = registry.conversation_dir(conversation_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text("{}")
        return httpx.Response(200, json={"id": str(conversation_id)})

    monkeypatch.setattr(registry, "get_or_create", get_or_create)
    monkeypatch.setattr(registry, "stop", AsyncMock())
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    def profiles(conversation_id):
        return LLMProfileStore(
            base_dir=registry.provisioning.runtime_dir(conversation_id)
            / "persistence"
            / "profiles"
        )

    with TestClient(app) as client:

        def start(name="title-aux", conversation_id=None):
            conversation_id = conversation_id or uuid4()
            response = client.post(
                "/api/conversations",
                json={
                    "conversation_id": str(conversation_id),
                    "agent": {
                        "kind": "Agent",
                        "llm": {"model": "openai/agent", "api_key": "agent-key"},
                        "tools": [],
                    },
                    "title_llm_profile": name,
                },
            )
            return conversation_id, response

        yield SimpleNamespace(
            start=start,
            profiles=profiles,
            host=host,
            registry=registry,
            attempts=attempts,
            client=client,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_docker_title_uses_only_selected_profile_with_runtime_credentials(
    title_start, tmp_path, linked
):
    t = title_start
    if linked:
        get_provider_connections_store().create(
            ProviderConnection(
                id="provider",
                display_name="Provider",
                provider="openai",
                api_key=SecretStr("title-aux-key"),
                base_url="http://title-provider/v1",
                created_at=0,
                updated_at=0,
            ),
            cipher=t.registry.config.cipher,
        )
        t.host.save(
            "title-aux",
            LLM(model="openai/title-aux", provider_connection_id="provider"),
            cipher=t.registry.config.cipher,
        )
    conversation_id, response = t.start()
    assert response.status_code == 200
    identity = t.registry.provisioning.load(conversation_id)
    profiles = t.profiles(conversation_id)
    assert profiles.list() == ["title-aux.json"]
    llm = profiles.load("title-aux", cipher=identity.cipher)
    assert llm.provider_connection_id is None
    assert llm.api_key == SecretStr("title-aux-key")
    if linked:
        assert llm.base_url == "http://title-provider/v1"
    payload = json.loads((profiles.base_dir / "title-aux.json").read_text())
    assert identity.cipher.decrypt(payload["api_key"]) == SecretStr("title-aux-key")
    assert t.registry.config.cipher.decrypt(payload["api_key"]) is None
    assert not list((profiles.base_dir.parent / "provider-connections").glob("*.json"))
    assert not any(
        b"title-aux-key" in path.read_bytes() or b"other-key" in path.read_bytes()
        for path in profiles.base_dir.parent.rglob("*")
        if path.is_file()
    )

    # The inner subscriber consumes the encrypted runtime store, not the host store.
    service = AsyncMock(spec=EventService)
    service.stored = StoredConversation(
        id=conversation_id,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        confirmation_policy=NeverConfirm(),
        title_llm_profile="title-aux",
    )
    service.cipher = identity.cipher
    saved = asyncio.Event()
    service.save_meta.side_effect = saved.set
    calls = []

    def completion(**kwargs):
        calls.append((kwargs["model"], kwargs["api_key"], kwargs.get("api_base")))
        return ModelResponse(
            choices=[
                {
                    "message": {"role": "assistant", "content": "Auxiliary title"},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ]
        )

    conversation = Conversation(
        agent=Agent(llm=LLM(model="acp-managed", usage_id="acp-managed"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    try:
        service._conversation = conversation
        with (
            patch(
                "openhands.agent_server.persistence.store.get_llm_profile_store",
                return_value=profiles,
            ),
            patch("openhands.sdk.llm.llm.litellm_completion", side_effect=completion),
        ):
            await AutoTitleSubscriber(service)(
                MessageEvent(
                    source="user",
                    llm_message=Message(
                        role="user", content=[TextContent(text="Fix the login bug")]
                    ),
                )
            )
            await asyncio.wait_for(saved.wait(), timeout=5)
    finally:
        conversation.close()
    assert service.stored.title == "Auxiliary title"
    assert calls == [
        (
            "title-aux",
            "title-aux-key",
            "http://title-provider/v1" if linked else None,
        )
    ]


def test_docker_title_snapshot_survives_host_edits_repeat_and_release(title_start):
    t = title_start
    conversation_id, response = t.start()
    assert response.status_code == 200
    profile = t.profiles(conversation_id).base_dir / "title-aux.json"
    snapshot = profile.read_bytes()
    t.host.save(
        "title-aux",
        LLM(model="openai/edited", api_key=SecretStr("edited")),
        include_secrets=True,
        cipher=t.registry.config.cipher,
    )
    for name in ("title-aux", "other", None):
        assert t.start(name, conversation_id)[1].status_code == 200
        assert profile.read_bytes() == snapshot
        assert t.profiles(conversation_id).list() == ["title-aux.json"]
    assert (
        t.client.delete(f"/api/conversations/{conversation_id}/runtime").status_code
        == 204
    )
    assert t.start("other", conversation_id)[1].status_code == 200
    assert profile.read_bytes() == snapshot
    assert t.profiles(conversation_id).list() == ["title-aux.json"]


@pytest.mark.parametrize("retry_name", ["other", None, "missing"])
def test_failed_docker_start_replaces_title_snapshot_on_retry(title_start, retry_name):
    t = title_start
    t.attempts.fail = True
    conversation_id, response = t.start()
    assert response.status_code == 500
    assert t.profiles(conversation_id).list() == ["title-aux.json"]
    t.attempts.fail = False
    assert t.start(retry_name, conversation_id)[1].status_code == 200
    assert t.profiles(conversation_id).list() == (
        ["other.json"] if retry_name == "other" else []
    )


@pytest.mark.parametrize("name", [None, "missing", "../escape", "broken", "dangling"])
def test_unavailable_docker_title_profile_keeps_creation_nonfatal(title_start, name):
    t = title_start
    (t.host.base_dir / "broken.json").write_text("not json")
    t.host.save("dangling", LLM(model="openai/aux", provider_connection_id="missing"))
    conversation_id, response = t.start(name)
    assert response.status_code == 200
    assert t.profiles(conversation_id).list() == []
    assert t.attempts.forwarded[conversation_id]["title_llm_profile"] == name


def test_concurrent_docker_starts_keep_the_winning_title_profile(
    title_start, monkeypatch
):
    t = title_start
    conversation_id = uuid4()
    first_forwarded = threading.Event()
    second_requested = threading.Event()
    original_post = httpx.AsyncClient.post
    snapshots = []

    async def post(self, url, **kwargs):
        if kwargs["json"]["title_llm_profile"] == "title-aux":
            first_forwarded.set()
            assert await asyncio.to_thread(second_requested.wait, 5)
            # Let the competing request reach the same creation window.
            await asyncio.sleep(0.1)
            snapshots.append(t.profiles(conversation_id).list())
        return await original_post(self, url, **kwargs)

    def second_start():
        assert first_forwarded.wait(5)
        second_requested.set()
        return t.start("other", conversation_id)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(t.start, "title-aux", conversation_id)
        second = pool.submit(second_start)
        assert first.result(timeout=10)[1].status_code == 200
        assert second.result(timeout=10)[1].status_code == 200
    assert snapshots == [["title-aux.json"]]
    assert t.profiles(conversation_id).list() == ["title-aux.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellations", [0, 1, 3])
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_delete_waits_for_title_preparation_before_removing_runtime(
    title_start, monkeypatch, cancellations, worker_fails
):
    t = title_start
    conversation_id = uuid4()
    preparing = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    order = []
    worker_error = OSError("synthetic profile preparation failure")
    prepare = t.registry.provisioning.prepare_title_profile
    begin_delete = t.registry.begin_delete

    def blocked_prepare(*args):
        preparing.set()
        try:
            assert release.wait(10)
            prepare(*args)
            if worker_fails:
                raise worker_error
        finally:
            order.append("worker finished")
            finished.set()

    async def observed_delete(deleting_id):
        order.append("delete entered")
        return await begin_delete(deleting_id)

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(
                {
                    "conversation_id": str(conversation_id),
                    "agent": {"kind": "Agent", "llm": {"model": "test"}},
                    "title_llm_profile": "title-aux",
                }
            ).encode(),
        }

    async def checkpoint():
        # Let previously scheduled task wakeups run before the next cancellation.
        loop = asyncio.get_running_loop()
        resumed = loop.create_future()
        loop.call_soon(resumed.set_result, None)
        await resumed

    request = Request({"type": "http", "app": t.client.app}, receive)
    monkeypatch.setattr(
        t.registry.provisioning, "prepare_title_profile", blocked_prepare
    )
    monkeypatch.setattr(t.registry, "begin_delete", observed_delete)
    starting = asyncio.create_task(start_conversation(request, include_skills=False))
    deletion = None
    try:
        assert await asyncio.to_thread(preparing.wait, 5)
        for _ in range(cancellations):
            starting.cancel()
            await checkpoint()
        exited_before_worker = starting.done()
        deletion = asyncio.create_task(delete_conversation(conversation_id, request))
        await checkpoint()
        assert not finished.is_set()
        release.set()
        if cancellations:
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await asyncio.wait_for(starting, 5)
            assert starting.cancelled()
            assert cancelled.value.__cause__ is (worker_error if worker_fails else None)
        elif worker_fails:
            with pytest.raises(HTTPException) as failure:
                await asyncio.wait_for(starting, 5)
            assert failure.value.status_code == 502
            assert failure.value.__cause__ is worker_error
        else:
            assert (await asyncio.wait_for(starting, 5)).status_code == 200
        assert (await asyncio.wait_for(deletion, 5)).status_code == 200
        assert await asyncio.to_thread(finished.wait, 5)
        assert not exited_before_worker
        assert order == ["worker finished", "delete entered"]
        assert not t.registry.provisioning.runtime_dir(conversation_id).exists()
        assert not t.registry.provisioning.manifest_path(conversation_id).exists()
        assert not t.registry.conversation_dir(conversation_id).exists()
    finally:
        release.set()
        tasks: list[Awaitable[Response]] = [
            task for task in (starting, deletion) if task is not None
        ]
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        if preparing.is_set():
            assert await asyncio.to_thread(finished.wait, 5)
        assert starting.done() and (deletion is None or deletion.done())
