import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.credential_binding import (
    LocalVersionedCredentialBinding,
    router,
)
from openhands.agent_server.models import StartConversationRequest, StoredConversation
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.agent import ACPAgent
from openhands.sdk.credential import (
    CredentialConflict,
    CredentialNeedsReauthentication,
    HttpVersionedCredentialBinding,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.workspace import LocalWorkspace


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


def test_activation_route_installs_http_binding(tmp_path) -> None:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    app = FastAPI()
    app.state.conversation_service = service
    app.include_router(router, prefix="/api")
    conversation_id = uuid4()

    response = TestClient(app).put(
        f"/api/conversations/{conversation_id}/credential-bindings/CODEX_AUTH_JSON",
        json={
            "url": "https://app.test/api/credential",
            "headers": {"Authorization": "Bearer scoped"},
        },
    )

    assert response.status_code == 204
    binding = service._credential_bindings[conversation_id]["CODEX_AUTH_JSON"]
    assert isinstance(binding, HttpVersionedCredentialBinding)
    assert binding.url == "https://app.test/api/credential"


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
async def test_resume_removes_legacy_persisted_credential(tmp_path) -> None:
    store = FileSecretsStore(tmp_path / "settings")
    agent = ACPAgent(acp_command=["codex-acp"], acp_server="codex")
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
        await event_service.close()
        store.set_secret("CODEX_AUTH_JSON", "canonical")

        _, started = await service.start_conversation(
            request.model_copy(
                update={"conversation_id": info.id, "secrets": {}},
            )
        )

        assert not started
        record = service._conversation_records[info.id]
        assert "CODEX_AUTH_JSON" not in record.stored.secrets
        base_state = (
            tmp_path / "conversations" / info.id.hex / "base_state.json"
        ).read_text(encoding="utf-8")
        assert "legacy-copy" not in base_state
