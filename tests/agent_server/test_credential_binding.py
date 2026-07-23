import json
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, call, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import openhands.agent_server.event_service as event_service_module
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.credential_binding import (
    LocalVersionedCredentialBinding,
    router,
)
from openhands.agent_server.event_service import (
    CredentialBindingActivationTooLate,
    EventService,
)
from openhands.agent_server.models import StartConversationRequest, StoredConversation
from openhands.agent_server.persistence import (
    CustomSecret,
    FileSecretsStore,
    Secrets,
)
from openhands.sdk import AgentContext
from openhands.sdk.agent import ACPAgent
from openhands.sdk.credential import (
    CredentialConflict,
    CredentialNeedsReauthentication,
    CredentialSyncError,
    HttpVersionedCredentialBinding,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.workspace import LocalWorkspace


@dataclass
class _CallbackState:
    responses: list[tuple[int, str]] = field(default_factory=list)
    authorizations: list[str | None] = field(default_factory=list)


@pytest.fixture
def credential_callback() -> Generator[tuple[str, _CallbackState]]:
    state = _CallbackState()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.authorizations.append(self.headers.get("Authorization"))
            response_status, body = state.responses.pop(0)
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/credential", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_local_binding_versions_and_conflicts(tmp_path) -> None:
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", "r0")
    binding = LocalVersionedCredentialBinding(store, "CODEX_AUTH_JSON")

    initial = await binding.load()
    successor = await binding.replace(initial.version, "r1")

    assert successor != initial.version
    assert (await binding.load()).value == "r1"
    with pytest.raises(CredentialConflict):
        await binding.replace(initial.version, "stale")
    assert (await binding.load()).value == "r1"


@pytest.mark.asyncio
async def test_local_binding_delete_recreate_changes_version(tmp_path) -> None:
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", "same")
    binding = LocalVersionedCredentialBinding(store, "CODEX_AUTH_JSON")
    first = await binding.load()

    assert store.delete_secret("CODEX_AUTH_JSON")
    store.set_secret("CODEX_AUTH_JSON", "same")
    second = await binding.load()

    assert second.version != first.version
    with pytest.raises(CredentialConflict):
        await binding.replace(first.version, "stale")


@pytest.mark.asyncio
async def test_local_binding_deleted_secret_requires_reauthentication(tmp_path) -> None:
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", "r0")
    binding = LocalVersionedCredentialBinding(store, "CODEX_AUTH_JSON")
    initial = await binding.load()
    assert store.delete_secret("CODEX_AUTH_JSON")

    with pytest.raises(CredentialNeedsReauthentication):
        await binding.replace(initial.version, "r1")


def test_local_versions_are_opaque_and_persisted(tmp_path) -> None:
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", "plaintext")
    value, version = store.load_versioned_secret("CODEX_AUTH_JSON")
    raw = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))

    assert value == "plaintext"
    assert version != "plaintext"
    assert raw["_credential_versions"]["CODEX_AUTH_JSON"] == version


def test_versioning_is_lazy_and_generic(tmp_path) -> None:
    store = FileSecretsStore(tmp_path)

    store.set_secret("OTHER", "value")

    raw = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert "_credential_versions" not in raw

    value, version = store.load_versioned_secret("OTHER")
    raw = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert value == "value"
    assert raw["_credential_versions"] == {"OTHER": version}

    store.set_secret("OTHER", "updated")
    updated_value, updated_version = store.load_versioned_secret("OTHER")
    assert updated_value == "updated"
    assert updated_version != version

    successor = store.replace_versioned_secret("OTHER", updated_version, "replaced")
    assert successor != updated_version
    assert store.load_versioned_secret("OTHER") == ("replaced", successor)


def test_whole_store_save_updates_credential_versions(tmp_path) -> None:
    store = FileSecretsStore(tmp_path)
    store.set_secret("CODEX_AUTH_JSON", "r0")
    _, initial_version = store.load_versioned_secret("CODEX_AUTH_JSON")

    replacement = Secrets(
        custom_secrets={
            "CODEX_AUTH_JSON": CustomSecret(
                name="CODEX_AUTH_JSON",
                secret=SecretStr("r1"),
            ),
            "OTHER": CustomSecret(
                name="OTHER",
                secret=SecretStr("unversioned"),
            ),
        }
    )
    store.save(replacement)
    _, replacement_version = store.load_versioned_secret("CODEX_AUTH_JSON")
    raw = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))

    assert replacement_version != initial_version
    assert "OTHER" not in raw["_credential_versions"]
    with pytest.raises(ValueError, match="credential_version_conflict"):
        store.replace_versioned_secret("CODEX_AUTH_JSON", initial_version, "stale")

    store.save(replacement)
    assert store.load_versioned_secret("CODEX_AUTH_JSON")[1] == replacement_version

    store.save(Secrets())
    raw = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert "CODEX_AUTH_JSON" not in raw.get("_credential_versions", {})

    store.save(replacement)
    recreated_version = store.load_versioned_secret("CODEX_AUTH_JSON")[1]
    assert recreated_version != replacement_version


def test_activation_route_installs_http_binding(
    tmp_path,
    credential_callback: tuple[str, _CallbackState],
) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    app = FastAPI()
    app.state.conversation_service = service
    app.include_router(router, prefix="/api")
    conversation_id = uuid4()
    callback_url, callback = credential_callback
    callback.responses.append((200, '{"value":"credential","version":"v1"}'))

    response = TestClient(app).put(
        f"/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON",
        json={
            "url": callback_url,
            "headers": {
                "Authorization": "Bearer scoped",
            },
        },
    )

    assert response.status_code == 204
    assert callback.authorizations == ["Bearer scoped"]
    binding = service._credential_bindings[conversation_id]["CODEX_AUTH_JSON"]
    assert isinstance(binding, HttpVersionedCredentialBinding)
    assert binding.url == callback_url
    assert binding.headers == {"Authorization": "Bearer scoped"}


def test_activation_route_rejects_unavailable_binding(
    tmp_path,
    credential_callback: tuple[str, _CallbackState],
) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    app = FastAPI()
    app.state.conversation_service = service
    app.include_router(router, prefix="/api")
    conversation_id = uuid4()
    callback_url, callback = credential_callback
    callback.responses.extend([(503, "{}")] * 3)

    sleep = AsyncMock()
    with patch("openhands.agent_server.credential_binding.asyncio.sleep", sleep):
        response = TestClient(app).put(
            f"/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON",
            json={
                "url": callback_url,
                "headers": {"Authorization": "Bearer scoped"},
            },
        )

    assert response.status_code == 502
    assert conversation_id not in service._credential_bindings
    assert callback.authorizations == ["Bearer scoped"] * 3
    assert sleep.await_args_list == [call(0.25), call(0.5)]


def test_activation_route_retries_transient_binding_failure(
    tmp_path,
    credential_callback: tuple[str, _CallbackState],
) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    app = FastAPI()
    app.state.conversation_service = service
    app.include_router(router, prefix="/api")
    conversation_id = uuid4()
    callback_url, callback = credential_callback
    callback.responses.extend(
        [
            (503, "{}"),
            (200, '{"value":"credential","version":"v1"}'),
        ]
    )
    sleep = AsyncMock()

    with patch("openhands.agent_server.credential_binding.asyncio.sleep", sleep):
        response = TestClient(app).put(
            f"/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON",
            json={
                "url": callback_url,
                "headers": {"Authorization": "Bearer scoped"},
            },
        )

    assert response.status_code == 204
    assert callback.authorizations == ["Bearer scoped"] * 2
    sleep.assert_awaited_once_with(0.25)
    assert conversation_id in service._credential_bindings


@pytest.mark.parametrize(
    ("callback_status", "body", "activation_status"),
    [
        (404, "{}", 422),
        (403, "{}", 403),
        (409, "{}", 409),
        (200, "not-json", 422),
    ],
)
def test_activation_route_rejects_terminal_binding_failure(
    tmp_path,
    credential_callback: tuple[str, _CallbackState],
    callback_status: int,
    body: str,
    activation_status: int,
) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    app = FastAPI()
    app.state.conversation_service = service
    app.include_router(router, prefix="/api")
    conversation_id = uuid4()
    callback_url, callback = credential_callback
    callback.responses.append((callback_status, body))

    response = TestClient(app).put(
        f"/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON",
        json={
            "url": callback_url,
            "headers": {"Authorization": "Bearer scoped"},
        },
    )

    assert response.status_code == activation_status
    assert callback.authorizations == ["Bearer scoped"]
    assert conversation_id not in service._credential_bindings


def test_activation_route_rejects_invalid_binding_url(tmp_path) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    app = FastAPI()
    app.state.conversation_service = service
    app.include_router(router, prefix="/api")
    conversation_id = uuid4()

    response = TestClient(app).put(
        f"/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON",
        json={
            "url": "http://example.com:abc/credential",
            "headers": {"Authorization": "Bearer scoped"},
        },
    )

    assert response.status_code == 422
    assert conversation_id not in service._credential_bindings


@pytest.mark.asyncio
async def test_direct_conversations_share_rotated_canonical_value(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret("CODEX_AUTH_JSON", "r0")
    service = ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    )
    agent = ACPAgent(acp_command=["codex-acp"], acp_server="codex")
    workspace = LocalWorkspace(working_dir=tmp_path / "workspace")

    first = await service._resolve_credential_bindings(
        StoredConversation(id=uuid4(), agent=agent, workspace=workspace)
    )
    first_binding = first["CODEX_AUTH_JSON"]
    initial = await first_binding.load()
    await first_binding.replace(initial.version, "r1")

    second = await service._resolve_credential_bindings(
        StoredConversation(id=uuid4(), agent=agent, workspace=workspace)
    )
    assert (await second["CODEX_AUTH_JSON"].load()).value == "r1"


@pytest.mark.asyncio
async def test_direct_start_strips_reserved_conversation_secret(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret("CODEX_AUTH_JSON", "canonical")
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("request-copy"))},
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        assert "CODEX_AUTH_JSON" not in event_service.stored.secrets
        assert "CODEX_AUTH_JSON" in event_service.credential_bindings


@pytest.mark.asyncio
async def test_managed_start_scrubs_all_durable_credential_copies(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret("CODEX_AUTH_JSON", "canonical")
    agent = ACPAgent(
        acp_command=["codex-acp"],
        acp_server="codex",
        agent_context=AgentContext(
            secrets={
                "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("context-copy")),
                "KEEP": StaticSecret(value=SecretStr("keep-context")),
            }
        ),
    )
    request = StartConversationRequest(
        agent=agent,
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        secrets={
            "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("request-copy")),
            "KEEP": StaticSecret(value=SecretStr("keep-request")),
        },
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        state = await event_service.get_state()

        assert "CODEX_AUTH_JSON" not in event_service.stored.secrets
        assert "KEEP" in event_service.stored.secrets
        assert event_service.stored.agent.agent_context is not None
        assert "CODEX_AUTH_JSON" not in (
            event_service.stored.agent.agent_context.secrets or {}
        )
        assert "KEEP" in (event_service.stored.agent.agent_context.secrets or {})
        assert "CODEX_AUTH_JSON" not in state.secret_registry.secret_sources
        assert "KEEP" in state.secret_registry.secret_sources
        assert state.agent.agent_context is not None
        assert "CODEX_AUTH_JSON" not in (state.agent.agent_context.secrets or {})

        conversation_dir = tmp_path / "conversations" / info.id.hex
        meta = json.loads((conversation_dir / "meta.json").read_text())
        base_state = json.loads((conversation_dir / "base_state.json").read_text())
        assert "CODEX_AUTH_JSON" not in meta["secrets"]
        assert "CODEX_AUTH_JSON" not in meta["agent"]["agent_context"]["secrets"]
        assert "CODEX_AUTH_JSON" not in base_state["agent"]["agent_context"]["secrets"]
        assert "CODEX_AUTH_JSON" not in base_state["secret_registry"]["secret_sources"]
        artifacts = json.dumps(meta) + json.dumps(base_state)
        assert "request-copy" not in artifacts
        assert "context-copy" not in artifacts


@pytest.mark.asyncio
async def test_late_binding_scrubs_open_uninitialized_conversation(tmp_path) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    request = StartConversationRequest(
        agent=ACPAgent(
            acp_command=["codex-acp"],
            acp_server="codex",
            agent_context=AgentContext(
                secrets={
                    "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("context-copy"))
                }
            ),
        ),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("request-copy"))},
    )
    binding = HttpVersionedCredentialBinding(
        "https://app.test/api/credential",
        {
            "Authorization": "Bearer initial",
        },
    )

    async with service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None

        activate_file_credential_binding = ACPAgent.activate_file_credential_binding

        def activate_while_locked(agent, secret_name, candidate) -> None:
            assert event_service._conversation is not None
            assert event_service._conversation._state.owned()
            activate_file_credential_binding(agent, secret_name, candidate)

        with patch.object(
            ACPAgent,
            "activate_file_credential_binding",
            autospec=True,
            side_effect=activate_while_locked,
        ):
            original_atomic_write = event_service_module.atomic_write_text
            writes = 0

            def fail_first_write(*args, **kwargs):
                nonlocal writes
                writes += 1
                if writes == 1:
                    raise OSError("write failed")
                return original_atomic_write(*args, **kwargs)

            with patch(
                "openhands.agent_server.event_service.atomic_write_text",
                side_effect=fail_first_write,
            ):
                with pytest.raises(OSError, match="write failed"):
                    await service.activate_credential_binding(
                        info.id,
                        "CODEX_AUTH_JSON",
                        binding,
                    )
                await service.activate_credential_binding(
                    info.id,
                    "CODEX_AUTH_JSON",
                    HttpVersionedCredentialBinding(
                        binding.url,
                        binding.headers,
                    ),
                )

        state = await event_service.get_state()
        assert event_service.credential_bindings["CODEX_AUTH_JSON"] is binding
        assert "CODEX_AUTH_JSON" not in event_service.stored.secrets
        assert "CODEX_AUTH_JSON" not in state.secret_registry.secret_sources
        assert event_service.stored.agent.agent_context is not None
        assert "CODEX_AUTH_JSON" not in (
            event_service.stored.agent.agent_context.secrets or {}
        )

        conversation_dir = tmp_path / "conversations" / info.id.hex
        meta = json.loads((conversation_dir / "meta.json").read_text())
        base_state = json.loads((conversation_dir / "base_state.json").read_text())
        assert "CODEX_AUTH_JSON" not in meta["secrets"]
        assert "CODEX_AUTH_JSON" not in meta["agent"]["agent_context"]["secrets"]
        assert "CODEX_AUTH_JSON" not in base_state["agent"]["agent_context"]["secrets"]
        assert "CODEX_AUTH_JSON" not in base_state["secret_registry"]["secret_sources"]
        durable = json.dumps(meta) + json.dumps(base_state)
        assert "request-copy" not in durable
        assert "context-copy" not in durable

        assert event_service._conversation is not None
        event_service._conversation.agent._initialized = True
        replacement = HttpVersionedCredentialBinding(
            "https://app.test/api/credential",
            {
                "Authorization": "Bearer successor",
            },
        )
        await service.activate_credential_binding(
            info.id,
            "CODEX_AUTH_JSON",
            replacement,
        )
        assert event_service.credential_bindings["CODEX_AUTH_JSON"] is binding
        assert binding.headers == {
            "Authorization": "Bearer successor",
        }
        artifacts = (conversation_dir / "meta.json").read_text() + (
            conversation_dir / "base_state.json"
        ).read_text()
        assert "Bearer successor" not in artifacts

        with pytest.raises(CredentialBindingActivationTooLate):
            await service.activate_credential_binding(
                info.id,
                "CODEX_AUTH_JSON",
                HttpVersionedCredentialBinding(
                    "https://other.test/api/credential",
                    {"Authorization": "Bearer wrong-url"},
                ),
            )


@pytest.mark.asyncio
async def test_first_binding_after_acp_initialization_is_rejected(tmp_path) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )

    async with service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        assert event_service._conversation is not None
        event_service._conversation.agent._initialized = True

        with pytest.raises(CredentialBindingActivationTooLate):
            await service.activate_credential_binding(
                info.id,
                "CODEX_AUTH_JSON",
                HttpVersionedCredentialBinding(
                    "https://app.test/api/credential",
                    {"Authorization": "Bearer too-late"},
                ),
            )


@pytest.mark.asyncio
async def test_live_plaintext_fallback_restarts_initialized_acp(tmp_path) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )

    async with service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        assert event_service._conversation is not None
        live_agent = event_service._conversation.agent
        assert isinstance(live_agent, ACPAgent)

        restart_for_updated_credentials = ACPAgent.restart_for_updated_credentials

        def restart_while_locked(agent, secret_names) -> None:
            assert event_service._conversation is not None
            assert event_service._conversation._state.owned()
            restart_for_updated_credentials(agent, secret_names)

        with patch.object(
            ACPAgent,
            "restart_for_updated_credentials",
            autospec=True,
            side_effect=restart_while_locked,
        ):
            await service.start_conversation(
                request.model_copy(
                    update={
                        "conversation_id": info.id,
                        "secrets": {
                            "CODEX_AUTH_JSON": StaticSecret(
                                value=SecretStr("fallback-before-init")
                            )
                        },
                    }
                )
            )
        assert not live_agent._restart_session_on_next_turn
        assert (
            "CODEX_AUTH_JSON"
            in live_agent._replace_file_credentials_on_next_materialisation
        )

        live_agent._initialized = True
        await service.start_conversation(
            request.model_copy(
                update={
                    "conversation_id": info.id,
                    "secrets": {
                        "CODEX_AUTH_JSON": StaticSecret(
                            value=SecretStr("fallback-after-init")
                        )
                    },
                }
            )
        )

        assert live_agent._restart_session_on_next_turn
        assert (
            "CODEX_AUTH_JSON"
            in live_agent._replace_file_credentials_on_next_materialisation
        )
        state = await event_service.get_state()
        assert "CODEX_AUTH_JSON" in state.secret_registry.secret_sources


@pytest.mark.asyncio
async def test_resume_uses_plaintext_fallback_without_binding(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    store.set_secret("CODEX_AUTH_JSON", "canonical")
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("initial-copy"))},
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    ) as service:
        info, _ = await service.start_conversation(request)
        assert service._event_services is not None
        event_service = service._event_services.pop(info.id)
        await event_service.close()
        store.delete_secret("CODEX_AUTH_JSON")

        _, started = await service.start_conversation(
            request.model_copy(
                update={
                    "conversation_id": info.id,
                    "secrets": {
                        "CODEX_AUTH_JSON": StaticSecret(
                            value=SecretStr("fallback-copy")
                        )
                    },
                }
            )
        )

        assert not started
        resumed = await service.get_event_service(info.id)
        assert resumed is not None
        state = await resumed.get_state()
        assert "CODEX_AUTH_JSON" not in resumed.credential_bindings
        assert "CODEX_AUTH_JSON" in resumed.stored.secrets
        assert "CODEX_AUTH_JSON" in state.secret_registry.secret_sources


@pytest.mark.asyncio
async def test_failed_resume_does_not_retain_plaintext_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        info, _ = await service.start_conversation(request)
        assert service._event_services is not None
        event_service = service._event_services.pop(info.id)
        await event_service.close()

        async def fail_load(conversation_id):
            assert conversation_id == info.id
            raise RuntimeError("resume failed")

        monkeypatch.setattr(
            service,
            "_get_or_load_event_service_locked",
            fail_load,
        )
        with pytest.raises(RuntimeError, match="resume failed"):
            await service.start_conversation(
                request.model_copy(
                    update={
                        "conversation_id": info.id,
                        "secrets": {
                            "CODEX_AUTH_JSON": StaticSecret(
                                value=SecretStr("temporary-fallback")
                            )
                        },
                    }
                )
            )

        record = service._conversation_records[info.id]
        assert "CODEX_AUTH_JSON" not in record.stored.secrets
        assert "temporary-fallback" not in record.stored.model_dump_json()


@pytest.mark.asyncio
async def test_start_failure_restores_binding_when_close_also_fails(tmp_path) -> None:
    conversation_id = uuid4()
    binding = HttpVersionedCredentialBinding(
        "https://app.test/api/credential",
        {"Authorization": "Bearer initial"},
    )
    request = StartConversationRequest(
        conversation_id=conversation_id,
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        service._credential_bindings[conversation_id] = {"CODEX_AUTH_JSON": binding}
        with (
            patch.object(
                EventService,
                "start",
                new=AsyncMock(side_effect=RuntimeError("start failed")),
            ),
            patch.object(
                EventService,
                "close",
                new=AsyncMock(side_effect=CredentialSyncError("close failed")),
            ),
            pytest.raises(RuntimeError, match="start failed"),
        ):
            await service.start_conversation(request)

        assert service._credential_bindings[conversation_id] == {
            "CODEX_AUTH_JSON": binding
        }


@pytest.mark.asyncio
async def test_non_owner_does_not_scrub_persisted_credential(tmp_path) -> None:
    conversations_dir = tmp_path / "conversations"
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("persisted-copy"))},
    )

    async with ConversationService(conversations_dir=conversations_dir) as owner:
        info, _ = await owner.start_conversation(request)
        conversation_dir = conversations_dir / info.id.hex
        before_meta = (conversation_dir / "meta.json").read_bytes()
        before_state = (conversation_dir / "base_state.json").read_bytes()

        canonical = FileSecretsStore(tmp_path / "settings")
        canonical.set_secret("CODEX_AUTH_JSON", "canonical")
        async with ConversationService(
            conversations_dir=conversations_dir,
            secrets_store=canonical,
        ) as non_owner:
            _, started = await non_owner.start_conversation(
                request.model_copy(update={"conversation_id": info.id, "secrets": {}})
            )
            assert not started

        assert (conversation_dir / "meta.json").read_bytes() == before_meta
        assert (conversation_dir / "base_state.json").read_bytes() == before_state


@pytest.mark.asyncio
async def test_resume_removes_legacy_persisted_credential(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    agent = ACPAgent(
        acp_command=["codex-acp"],
        acp_server="codex",
        agent_context=AgentContext(
            secrets={
                "CODEX_AUTH_JSON": StaticSecret(value=SecretStr("legacy-context-copy")),
                "KEEP": StaticSecret(value=SecretStr("keep-context")),
            }
        ),
    )
    workspace = LocalWorkspace(working_dir=tmp_path / "workspace")
    request = StartConversationRequest(
        agent=agent,
        workspace=workspace,
        secrets={"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("legacy-copy"))},
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    ) as service:
        info, _ = await service.start_conversation(request)
        assert service._event_services is not None
        event_service = service._event_services.pop(info.id)
        state = await event_service.get_state()
        state.tags = {"persist": "registry"}
        await event_service.close()
        store.set_secret(
            "CODEX_AUTH_JSON",
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {"refresh_token": "canonical-refresh"},
                }
            ),
        )
        conversation_dir = tmp_path / "conversations" / info.id.hex
        legacy_auth_file = conversation_dir / "acp" / "codex" / "auth.json"
        legacy_auth_file.parent.mkdir(parents=True)
        legacy_auth_file.write_text("legacy-file-copy", encoding="utf-8")

        _, started = await service.start_conversation(
            request.model_copy(
                update={"conversation_id": info.id, "secrets": {}},
            )
        )

        assert not started
        record = service._conversation_records[info.id]
        assert "CODEX_AUTH_JSON" not in record.stored.secrets
        meta = json.loads((conversation_dir / "meta.json").read_text())
        base_state = json.loads((conversation_dir / "base_state.json").read_text())
        assert "CODEX_AUTH_JSON" not in meta["secrets"]
        assert "CODEX_AUTH_JSON" not in meta["agent"]["agent_context"]["secrets"]
        assert "KEEP" in meta["agent"]["agent_context"]["secrets"]
        assert "CODEX_AUTH_JSON" not in base_state["agent"]["agent_context"]["secrets"]
        assert "KEEP" in base_state["agent"]["agent_context"]["secrets"]
        assert "CODEX_AUTH_JSON" not in base_state["secret_registry"]["secret_sources"]
        assert "KEEP" in base_state["secret_registry"]["secret_sources"]
        assert not legacy_auth_file.exists()
        assert legacy_auth_file.parent.exists()


@pytest.mark.asyncio
async def test_failed_canonical_preflight_preserves_legacy_auth_file(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    request = StartConversationRequest(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex"),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        secrets_store=store,
    ) as service:
        info, _ = await service.start_conversation(request)
        assert service._event_services is not None
        event_service = service._event_services.pop(info.id)
        await event_service.close()
        conversation_dir = tmp_path / "conversations" / info.id.hex
        legacy_auth_file = conversation_dir / "acp" / "codex" / "auth.json"
        legacy_auth_file.parent.mkdir(parents=True)
        legacy_auth_file.write_text("only-legacy-copy", encoding="utf-8")
        store.set_secret("CODEX_AUTH_JSON", "invalid-canonical")

        with pytest.raises(CredentialNeedsReauthentication):
            await service.start_conversation(
                request.model_copy(update={"conversation_id": info.id})
            )

        assert legacy_auth_file.read_text(encoding="utf-8") == "only-legacy-copy"
