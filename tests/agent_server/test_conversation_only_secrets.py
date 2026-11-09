"""Conversation-only secrets use the real API, state store and resume path."""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_secrets_store,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.profiles.agent_profile import ACPAgentProfile
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


def _client(service: ConversationService, *, cipher: Cipher | None) -> AsyncClient:
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    app.state.config = Config(
        session_api_keys=[],
        secret_key=SecretStr(cipher.secret_key) if cipher else None,
    )
    app.dependency_overrides[get_conversation_service] = lambda: service
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _start(service: ConversationService, workspace_dir):
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
            workspace=LocalWorkspace(working_dir=workspace_dir),
        )
    )
    return info.id


@pytest.mark.asyncio
async def test_conversation_secret_is_encrypted_isolated_and_restored(tmp_path):
    cipher = Cipher("conversation-test-key")
    root = tmp_path / "conversations"
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        first = await _start(service, tmp_path / "first")
        second = await _start(service, tmp_path / "second")
        async with _client(service, cipher=cipher) as client:
            url = f"/api/conversations/{first}/conversation-secrets"
            response = await client.post(
                url, json={"name": "ONLY_FIRST", "value": "unique-private-value"}
            )
            assert response.status_code == 200
            assert response.json() == {"name": "ONLY_FIRST"}
            assert "unique-private-value" not in response.text
            duplicate = await client.post(
                url, json={"name": "ONLY_FIRST", "value": "replacement"}
            )
            assert duplicate.status_code == 409
        first_state = await (await service.get_event_service(first)).get_state()
        second_state = await (await service.get_event_service(second)).get_state()
        assert first_state.secret_registry.get_secret_value("ONLY_FIRST") == (
            "unique-private-value"
        )
        assert "ONLY_FIRST" not in second_state.secret_registry.secret_sources
        assert get_secrets_store().get_secret("ONLY_FIRST") is None
        fork = await service.fork_conversation(first)
        assert fork is not None
        fork_state = await (await service.get_event_service(fork.id)).get_state()
        assert "ONLY_FIRST" not in fork_state.secret_registry.secret_sources
        saved = (root / first.hex / "base_state.json").read_text()
        assert "unique-private-value" not in saved
        assert json.loads(saved)["secret_registry"]["secret_sources"]
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        state = await (await service.get_event_service(first)).get_state()
        assert state.secret_registry.get_secret_value("ONLY_FIRST") == (
            "unique-private-value"
        )


@pytest.mark.asyncio
async def test_rejects_secret_outside_launched_profile_allowlist(tmp_path):
    cipher = Cipher("conversation-test-key")
    profile = ACPAgentProfile(
        name="restricted", acp_server="codex", secret_refs=["ALLOWED"]
    )
    get_agent_profile_store().save(profile)
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", cipher=cipher
    ) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent_profile_id=profile.id,
                workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
            )
        )
        async with _client(service, cipher=cipher) as client:
            response = await client.post(
                f"/api/conversations/{info.id}/conversation-secrets",
                json={"name": "DENIED", "value": "not-allowed"},
            )
            assert response.status_code == 403
            allowed = await client.post(
                f"/api/conversations/{info.id}/conversation-secrets",
                json={"name": "ALLOWED", "value": "approved-value"},
            )
            assert allowed.status_code == 200
        state = await (await service.get_event_service(info.id)).get_state()
        assert "DENIED" not in state.secret_registry.secret_sources
        assert state.secret_registry.get_secret_value("ALLOWED") == "approved-value"


@pytest.mark.asyncio
async def test_no_cipher_or_invalid_value_never_adds_secret(tmp_path):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        conversation_id = await _start(service, tmp_path / "workspace")
        async with _client(service, cipher=None) as client:
            url = f"/api/conversations/{conversation_id}/conversation-secrets"
            assert (
                await client.post(url, json={"name": "TOKEN", "value": "private"})
            ).status_code == 409
            assert (
                await client.post(url, json={"name": "invalid-name", "value": "x"})
            ).status_code == 422
            assert (
                await client.post(url, json={"name": "TOKEN", "value": "  "})
            ).status_code == 422
            assert (
                await client.post(url, json={"name": "TOKEN", "value": "**********"})
            ).status_code == 422
        state = await (await service.get_event_service(conversation_id)).get_state()
        assert "TOKEN" not in state.secret_registry.secret_sources
