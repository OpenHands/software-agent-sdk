import asyncio
import base64
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server import codex_auth as codex_auth_module
from openhands.agent_server.api import create_app
from openhands.agent_server.codex_auth import (
    CODEX_AUTH_SECRET_NAME,
    CodexAuthBroker,
    router,
)
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.secret import LookupSecret
from openhands.sdk.workspace import LocalWorkspace


def _auth_value(
    *,
    access_token: str = "access-r0",
    refresh_token: str = "refresh-r0",
) -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": "id-r0",
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        },
        separators=(",", ":"),
    )


def _local_source() -> LookupSecret:
    return LookupSecret(
        url="http://agent/api/settings/secrets/CODEX_AUTH_JSON",
        headers={"X-Session-API-Key": "broad-session-key"},
    )


def _token(source: LookupSecret) -> str:
    return source.headers["X-OH-Codex-Token"]


def _refresh_authorization(source: LookupSecret) -> str:
    encoded = base64.b64encode(f"codex:{_token(source)}".encode()).decode()
    return f"Basic {encoded}"


@pytest.fixture
def broker_client(tmp_path):
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", _auth_value())
    broker = CodexAuthBroker(store)
    app = FastAPI()
    app.include_router(router)
    app.state.conversation_service = SimpleNamespace(codex_auth_broker=broker)
    with TestClient(app) as client:
        yield client, broker, store


def test_local_lookup_uses_scoped_non_disclosing_capability(tmp_path):
    store = FileSecretsStore(tmp_path)
    broker = CodexAuthBroker(store)
    conversation_id = uuid4()

    source = broker.ensure_brokered_source(conversation_id, _local_source())
    token = _token(source)

    assert source.headers == {"X-OH-Codex-Token": token}
    assert "broad-session-key" not in source.headers.values()
    assert urlsplit(source.url).netloc == "agent"
    assert urlsplit(source.url).path == (
        f"/api/conversations/{conversation_id}/codex-auth"
    )
    assert broker.is_authorized(conversation_id, token)
    assert token not in source.model_dump_json()


def test_local_lookup_reuses_conversation_capability(tmp_path):
    broker = CodexAuthBroker(FileSecretsStore(tmp_path))
    conversation_id = uuid4()

    first = broker.ensure_brokered_source(conversation_id, _local_source())
    second = broker.ensure_brokered_source(conversation_id, _local_source())

    assert _token(second) == _token(first)
    assert broker.is_authorized(conversation_id, _token(first))


def test_broker_leaves_saas_source_unchanged(tmp_path):
    broker = CodexAuthBroker(FileSecretsStore(tmp_path))
    source = LookupSecret(
        url="https://cloud/api/internal/conversations/123/codex-auth",
        headers={"X-OH-Sandbox": "sandbox-key", "X-OH-Codex": "cloud-token"},
    )

    assert broker.ensure_brokered_source(uuid4(), source) is source


def test_file_store_compare_and_swap_rejects_concurrent_loser(tmp_path):
    store = FileSecretsStore(tmp_path)
    original = _auth_value()
    store.set_secret("CODEX_AUTH_JSON", original, description="Codex auth")
    digest = hashlib.sha256(original.encode()).hexdigest()
    candidates = [
        _auth_value(access_token="access-a", refresh_token="refresh-a"),
        _auth_value(access_token="access-b", refresh_token="refresh-b"),
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda value: store.compare_and_swap_secret(
                    "CODEX_AUTH_JSON", digest, value
                ),
                candidates,
            )
        )

    assert sorted(results) == [False, True]
    stored = store.load()
    assert stored is not None
    assert stored.custom_secrets["CODEX_AUTH_JSON"].description == "Codex auth"
    assert store.get_secret("CODEX_AUTH_JSON") in candidates


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "compare_and_swap"])
async def test_broker_store_io_does_not_block_event_loop(
    tmp_path, monkeypatch, operation
):
    store = FileSecretsStore(tmp_path)
    value = _auth_value()
    store.set_secret(CODEX_AUTH_SECRET_NAME, value)
    broker = CodexAuthBroker(store)
    started = threading.Event()
    release = threading.Event()

    if operation == "get":

        def blocking_get(_name):
            started.set()
            release.wait(2)
            return value

        monkeypatch.setattr(store, "get_secret", blocking_get)
    else:

        def blocking_compare(_name, _digest, _value):
            started.set()
            release.wait(2)
            return True

        monkeypatch.setattr(store, "compare_and_swap_secret", blocking_compare)

    async def run_operation() -> object:
        if operation == "get":
            return await broker.get_value()
        return await broker.compare_and_swap("digest", value)

    task = asyncio.create_task(run_operation())
    start = time.monotonic()
    while not started.is_set():
        await asyncio.sleep(0)
    assert time.monotonic() - start < 1
    release.set()
    assert await asyncio.wait_for(task, timeout=1) in (value, True)


def test_broker_get_update_and_release_are_capability_scoped(broker_client):
    client, broker, store = broker_client
    conversation_id = uuid4()
    source = broker.ensure_brokered_source(conversation_id, _local_source())
    path = urlsplit(source.url).path
    headers = source.headers
    original = store.get_secret("CODEX_AUTH_JSON")
    assert original is not None
    original_digest = hashlib.sha256(original.encode()).hexdigest()

    unauthorized = client.get(path, headers={"X-Session-API-Key": "broad-session-key"})
    wrong_scope = client.get(
        path.replace(str(conversation_id), str(uuid4())), headers=headers
    )
    fetched = client.get(path, headers=headers)
    touched = client.head(path, headers=headers)

    assert unauthorized.status_code == 401
    assert wrong_scope.status_code == 401
    assert fetched.status_code == 200
    assert fetched.text == original
    assert fetched.headers["Cache-Control"] == "no-store"
    assert touched.status_code == 204
    assert touched.headers["X-Codex-Auth-Digest"] == original_digest

    updated = _auth_value(access_token="access-r1", refresh_token="refresh-r1")
    winner = client.put(
        path,
        headers=headers,
        json={"expected_digest": original_digest, "value": updated},
    )
    loser = client.put(
        path,
        headers=headers,
        json={"expected_digest": original_digest, "value": original},
    )

    assert winner.status_code == 204
    assert loser.status_code == 409
    assert store.get_secret("CODEX_AUTH_JSON") == updated

    released = client.delete(path, headers=headers)
    revoked = client.get(path, headers=headers)

    assert released.status_code == 204
    assert revoked.status_code == 401


def test_full_app_exempts_only_capability_broker_from_session_auth(tmp_path):
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", _auth_value())
    broker = CodexAuthBroker(store)
    conversation_id = uuid4()
    source = broker.ensure_brokered_source(conversation_id, _local_source())
    app = create_app(Config(session_api_keys=["broad-session-key"]))
    app.state.conversation_service = SimpleNamespace(codex_auth_broker=broker)
    client = TestClient(app)

    broker_response = client.get(urlsplit(source.url).path, headers=source.headers)
    settings_response = client.get("/api/settings")

    assert broker_response.status_code == 200
    assert settings_response.status_code == 401


def test_sequential_stale_refresh_converges_without_second_rotation(
    broker_client, monkeypatch
):
    client, broker, store = broker_client
    first_source = broker.ensure_brokered_source(uuid4(), _local_source())
    calls = 0

    async def refresh(refresh_token: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert refresh_token == "refresh-r0"
        return httpx.Response(
            200,
            json={"access_token": "access-r1", "refresh_token": "refresh-r1"},
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
        )

    monkeypatch.setattr(codex_auth_module, "_request_token_refresh", refresh)
    payload = {
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "grant_type": "refresh_token",
        "refresh_token": "refresh-r0",
    }

    first = client.post(
        f"{urlsplit(first_source.url).path}/refresh",
        headers={"Authorization": _refresh_authorization(first_source)},
        json=payload,
    )
    second_source = broker.ensure_brokered_source(uuid4(), _local_source())
    second = client.post(
        f"{urlsplit(second_source.url).path}/refresh",
        headers={"Authorization": _refresh_authorization(second_source)},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["refresh_token"] == "refresh-r1"
    assert calls == 1
    assert (
        json.loads(store.get_secret("CODEX_AUTH_JSON") or "{}")["tokens"][
            "refresh_token"
        ]
        == "refresh-r1"
    )


def test_concurrent_refresh_rotates_upstream_once(broker_client, monkeypatch):
    client, broker, _store = broker_client
    sources = [
        broker.ensure_brokered_source(uuid4(), _local_source()) for _index in range(2)
    ]
    calls = 0

    async def refresh(_refresh_token: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            json={"access_token": "access-r1", "refresh_token": "refresh-r1"},
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
        )

    monkeypatch.setattr(codex_auth_module, "_request_token_refresh", refresh)
    payload = {
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "grant_type": "refresh_token",
        "refresh_token": "refresh-r0",
    }

    def submit(source: LookupSecret) -> httpx.Response:
        return client.post(
            f"{urlsplit(source.url).path}/refresh",
            headers={"Authorization": _refresh_authorization(source)},
            json=payload,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, sources))

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert calls == 1


@pytest.mark.asyncio
async def test_update_waits_for_in_flight_refresh(tmp_path, monkeypatch):
    store = FileSecretsStore(tmp_path)
    original = _auth_value()
    store.set_secret("CODEX_AUTH_JSON", original)
    broker = CodexAuthBroker(store)
    app = FastAPI()
    app.include_router(router)
    app.state.conversation_service = SimpleNamespace(codex_auth_broker=broker)
    source = broker.ensure_brokered_source(uuid4(), _local_source())
    refresh_started = asyncio.Event()
    continue_refresh = asyncio.Event()

    async def refresh(_refresh_token: str) -> httpx.Response:
        refresh_started.set()
        await continue_refresh.wait()
        return httpx.Response(
            200,
            json={"access_token": "access-r1", "refresh_token": "refresh-r1"},
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
        )

    monkeypatch.setattr(codex_auth_module, "_request_token_refresh", refresh)
    path = urlsplit(source.url).path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        refresh_task = asyncio.create_task(
            client.post(
                f"{path}/refresh",
                headers={"Authorization": _refresh_authorization(source)},
                json={
                    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                    "grant_type": "refresh_token",
                    "refresh_token": "refresh-r0",
                },
            )
        )
        await asyncio.wait_for(refresh_started.wait(), timeout=1)
        update_task = asyncio.create_task(
            client.put(
                path,
                headers=source.headers,
                json={
                    "expected_digest": hashlib.sha256(original.encode()).hexdigest(),
                    "value": _auth_value(
                        access_token="local", refresh_token="refresh-r0"
                    ),
                },
            )
        )
        await asyncio.sleep(0.01)
        assert not update_task.done()
        continue_refresh.set()
        refresh_response, update_response = await asyncio.gather(
            refresh_task, update_task
        )

    assert refresh_response.status_code == 200
    assert update_response.status_code == 409
    assert json.loads(store.get_secret("CODEX_AUTH_JSON") or "{}")["tokens"] == {
        "id_token": "id-r0",
        "access_token": "access-r1",
        "refresh_token": "refresh-r1",
    }


def test_failed_refresh_preserves_authoritative_secret(broker_client, monkeypatch):
    client, broker, store = broker_client
    conversation_id = uuid4()
    source = broker.ensure_brokered_source(conversation_id, _local_source())
    path = f"{urlsplit(source.url).path}/refresh"
    original = store.get_secret("CODEX_AUTH_JSON")

    async def refresh(_refresh_token: str) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"code": "upstream_failed"}},
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
        )

    monkeypatch.setattr(codex_auth_module, "_request_token_refresh", refresh)
    response = client.post(
        path,
        headers={"Authorization": _refresh_authorization(source)},
        json={
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "grant_type": "refresh_token",
            "refresh_token": "refresh-r0",
        },
    )

    assert response.status_code == 502
    assert store.get_secret("CODEX_AUTH_JSON") == original


@pytest.mark.asyncio
async def test_restart_reissues_capability_and_keeps_it_out_of_meta(tmp_path):
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret("CODEX_AUTH_JSON", _auth_value())
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        secrets={"CODEX_AUTH_JSON": _local_source()},
    )
    first_broker = CodexAuthBroker(store)

    async with ConversationService(
        conversations_dir=conversations_dir,
        codex_auth_broker=first_broker,
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        first_source = (
            event_service.get_conversation().state.secret_registry.secret_sources[
                "CODEX_AUTH_JSON"
            ]
        )
        assert isinstance(first_source, LookupSecret)
        first_token = _token(first_source)
        assert event_service.stored.secrets["CODEX_AUTH_JSON"] == _local_source()
        meta = (conversations_dir / info.id.hex / "meta.json").read_text()
        assert first_token not in meta
        assert "/api/settings/secrets/CODEX_AUTH_JSON" in meta

    assert not first_broker.is_authorized(info.id, first_token)

    second_broker = CodexAuthBroker(store)
    async with ConversationService(
        conversations_dir=conversations_dir,
        codex_auth_broker=second_broker,
    ) as service:
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        second_source = (
            event_service.get_conversation().state.secret_registry.secret_sources[
                "CODEX_AUTH_JSON"
            ]
        )
        assert isinstance(second_source, LookupSecret)
        second_token = _token(second_source)
        assert second_token != first_token
        assert second_broker.is_authorized(info.id, second_token)
        assert not second_broker.is_authorized(info.id, first_token)


@pytest.mark.asyncio
async def test_agent_context_secret_is_brokered_after_registry_initialization(tmp_path):
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret(CODEX_AUTH_SECRET_NAME, _auth_value())
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = StartConversationRequest(
        agent=Agent(
            llm=LLM(model="gpt-4o", usage_id="test"),
            tools=[],
            agent_context=AgentContext(
                secrets={CODEX_AUTH_SECRET_NAME: _local_source()}
            ),
        ),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
    )
    broker = CodexAuthBroker(store)

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        codex_auth_broker=broker,
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        source = event_service.get_conversation().state.secret_registry.secret_sources[
            CODEX_AUTH_SECRET_NAME
        ]
        assert isinstance(source, LookupSecret)
        assert source != _local_source()
        first_token = _token(source)
        assert broker.is_authorized(info.id, first_token)

    second_broker = CodexAuthBroker(store)
    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        codex_auth_broker=second_broker,
    ) as service:
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        source = event_service.get_conversation().state.secret_registry.secret_sources[
            CODEX_AUTH_SECRET_NAME
        ]
        assert isinstance(source, LookupSecret)
        second_token = _token(source)
        assert second_token != first_token
        assert second_broker.is_authorized(info.id, second_token)


@pytest.mark.asyncio
async def test_late_codex_auth_secret_update_is_brokered(tmp_path):
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret(CODEX_AUTH_SECRET_NAME, _auth_value())
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
    )
    broker = CodexAuthBroker(store)

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        codex_auth_broker=broker,
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        await event_service.update_secrets({CODEX_AUTH_SECRET_NAME: _local_source()})
        source = event_service.get_conversation().state.secret_registry.secret_sources[
            CODEX_AUTH_SECRET_NAME
        ]
        assert isinstance(source, LookupSecret)
        assert source != _local_source()
        assert broker.is_authorized(info.id, _token(source))


@pytest.mark.asyncio
async def test_conversation_delete_revokes_unmaterialized_capability(tmp_path):
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret("CODEX_AUTH_JSON", _auth_value())
    broker = CodexAuthBroker(store)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        secrets={"CODEX_AUTH_JSON": _local_source()},
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        codex_auth_broker=broker,
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        source = event_service.get_conversation().state.secret_registry.secret_sources[
            "CODEX_AUTH_JSON"
        ]
        assert isinstance(source, LookupSecret)
        token = _token(source)
        assert broker.is_authorized(info.id, token)

        assert await service.delete_conversation(info.id)
        assert not broker.is_authorized(info.id, token)
