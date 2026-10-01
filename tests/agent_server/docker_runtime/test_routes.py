import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

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
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.docker_runtime.routers import (
    delete_conversation,
    docker_conversation_router,
    proxy_conversation,
)
from openhands.agent_server.event_router import event_read_router
from openhands.agent_server.models import UpdateSecretsRequest
from openhands.agent_server.persistence import get_llm_profile_store
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import LookupSecret
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


class _DockerStartHarness:
    """Docker start route with the container and the inner HTTP boundary faked.

    The inner double behaves like the real inner server for what these tests
    observe: a successful start writes ``meta.json`` into the conversation
    directory, and a repeated start for the same id returns the existing
    conversation. ``inner_fails`` makes the inner request fail so a creation
    attempt stays incomplete.
    """

    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
        self.config = Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
        )
        self.host_store = get_llm_profile_store()
        self.save_host_profile("title-aux", "openai/aux", "aux-secret")
        self.registry = DockerConversationRegistry(self.config)
        self.seen: dict = {}
        self.posted: list[dict] = []
        self.inner_fails = False
        harness = self

        async def get_or_create(conversation_id):
            harness.seen["profiles_when_container_starts"] = harness.runtime_profiles(
                conversation_id
            )
            harness.seen["identity"] = harness.registry.provisioning.load(
                conversation_id
            )
            return SimpleNamespace(host="http://inner", api_key="inner-key")

        monkeypatch.setattr(self.registry, "get_or_create", get_or_create)

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return None

            async def post(self, url, **kwargs):
                if harness.inner_fails:
                    raise httpx.ConnectError("inner runtime unavailable")
                harness.posted.append({"url": url, **kwargs})
                conversation_id = kwargs["json"]["conversation_id"]
                conversation_dir = harness.registry.conversation_dir(
                    UUID(conversation_id)
                )
                conversation_dir.mkdir(parents=True, exist_ok=True)
                (conversation_dir / "meta.json").write_text("{}")
                return SimpleNamespace(
                    status_code=200,
                    content=b"{}",
                    is_error=False,
                    json=lambda: {"id": conversation_id},
                )

        monkeypatch.setattr(
            "openhands.agent_server.docker_runtime.routers.httpx.AsyncClient",
            FakeClient,
        )
        app = FastAPI()
        app.state.conversation_registry = self.registry
        app.state.conversation_service = AsyncMock()
        app.include_router(docker_conversation_router, prefix="/api")
        self.client = TestClient(app)

    def save_host_profile(self, name: str, model: str, api_key: str) -> None:
        self.host_store.save(
            name,
            LLM(model=model, api_key=SecretStr(api_key)),
            include_secrets=True,
            cipher=self.config.cipher,
        )

    def start(self, conversation_id: UUID, title_llm_profile: str | None):
        body = StartConversationRequest(
            conversation_id=conversation_id,
            agent=Agent(llm=LLM(model="test"), tools=[]),
            workspace=LocalWorkspace(working_dir="/workspace"),
            title_llm_profile=title_llm_profile,
        ).model_dump(mode="json")
        return self.client.post("/api/conversations", json=body)

    def runtime_profiles(self, conversation_id: UUID) -> list[str]:
        profiles = self.registry.provisioning.persistence_dir(conversation_id)
        return sorted(path.name for path in (profiles / "profiles").glob("*.json"))

    def runtime_profile_bytes(self, conversation_id: UUID, name: str) -> bytes:
        profiles = self.registry.provisioning.persistence_dir(conversation_id)
        return (profiles / "profiles" / f"{name}.json").read_bytes()

    def load_runtime_profile(self, conversation_id: UUID, name: str) -> LLM:
        identity = self.registry.provisioning.load(conversation_id)
        profiles = self.registry.provisioning.persistence_dir(conversation_id)
        return LLMProfileStore(base_dir=profiles / "profiles").load(
            name, cipher=identity.cipher
        )


def test_start_conversation_stages_the_title_profile_before_the_runtime_starts(
    tmp_path, monkeypatch
):
    harness = _DockerStartHarness(tmp_path, monkeypatch)
    conversation_id = uuid4()

    response = harness.start(conversation_id, "title-aux")

    assert response.status_code == 200
    assert harness.seen["profiles_when_container_starts"] == ["title-aux.json"]
    staged = harness.load_runtime_profile(conversation_id, "title-aux")
    assert isinstance(staged.api_key, SecretStr)
    assert staged.api_key.get_secret_value() == "aux-secret"
    # The wire contract is unchanged: the runtime still receives only the name.
    assert harness.posted[-1]["json"]["title_llm_profile"] == "title-aux"
    assert "aux-secret" not in json.dumps(harness.posted[-1]["json"])


def test_start_conversation_with_a_missing_title_profile_still_creates_the_runtime(
    tmp_path, monkeypatch
):
    harness = _DockerStartHarness(tmp_path, monkeypatch)
    conversation_id = uuid4()

    response = harness.start(conversation_id, "absent")

    assert response.status_code == 200
    assert harness.seen["profiles_when_container_starts"] == []
    assert not harness.registry.provisioning.persistence_dir(conversation_id).exists()
    assert harness.posted[-1]["json"]["title_llm_profile"] == "absent"


def test_repeated_start_keeps_the_original_title_profile_snapshot(
    tmp_path, monkeypatch
):
    harness = _DockerStartHarness(tmp_path, monkeypatch)
    conversation_id = uuid4()
    assert harness.start(conversation_id, "title-aux").status_code == 200
    original = harness.runtime_profile_bytes(conversation_id, "title-aux")

    harness.save_host_profile("title-aux", "openai/host-edit", "rotated-secret")
    assert harness.start(conversation_id, "title-aux").status_code == 200

    assert harness.runtime_profile_bytes(conversation_id, "title-aux") == original
    staged = harness.load_runtime_profile(conversation_id, "title-aux")
    assert staged.model == "openai/aux"
    assert isinstance(staged.api_key, SecretStr)
    assert staged.api_key.get_secret_value() == "aux-secret"


def test_repeated_start_with_another_profile_does_not_add_it(tmp_path, monkeypatch):
    harness = _DockerStartHarness(tmp_path, monkeypatch)
    harness.save_host_profile("other", "openai/other", "other-secret")
    conversation_id = uuid4()
    assert harness.start(conversation_id, "title-aux").status_code == 200

    assert harness.start(conversation_id, "other").status_code == 200

    assert harness.runtime_profiles(conversation_id) == ["title-aux.json"]
    # The request itself is still forwarded unchanged; the runtime decides.
    assert harness.posted[-1]["json"]["title_llm_profile"] == "other"


def test_repeated_start_does_not_seed_a_conversation_created_without_a_profile(
    tmp_path, monkeypatch
):
    harness = _DockerStartHarness(tmp_path, monkeypatch)
    conversation_id = uuid4()
    assert harness.start(conversation_id, None).status_code == 200

    assert harness.start(conversation_id, "title-aux").status_code == 200

    assert harness.runtime_profiles(conversation_id) == []


@pytest.mark.parametrize("retried_profile", [None, "other"], ids=["none", "other"])
def test_retry_after_a_failed_creation_stages_only_the_retried_profile(
    tmp_path, monkeypatch, retried_profile
):
    harness = _DockerStartHarness(tmp_path, monkeypatch)
    harness.save_host_profile("other", "openai/other", "other-secret")
    conversation_id = uuid4()
    harness.inner_fails = True
    assert harness.start(conversation_id, "title-aux").status_code == 502
    assert harness.runtime_profiles(conversation_id) == ["title-aux.json"]
    assert not harness.registry.conversation_dir(conversation_id).exists()

    harness.inner_fails = False
    assert harness.start(conversation_id, retried_profile).status_code == 200

    expected = [] if retried_profile is None else [f"{retried_profile}.json"]
    assert harness.runtime_profiles(conversation_id) == expected
