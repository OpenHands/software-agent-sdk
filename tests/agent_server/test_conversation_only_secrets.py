"""Conversation configuration additions use the real API and resume path."""

import json
import sys
from pathlib import Path

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
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_secrets_store,
    get_settings_store,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    LaunchedAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.settings.model import OpenHandsAgentSettings
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
            url = f"/api/conversations/{first}/configuration/changes"
            response = await client.post(
                url,
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "ONLY_FIRST",
                            "value": "unique-private-value",
                        }
                    ]
                },
            )
            assert response.status_code == 200
            assert response.json() == {
                "applied": [{"type": "secret.grant", "name": "ONLY_FIRST"}],
            }
            assert "unique-private-value" not in response.text
            duplicate = await client.post(
                url,
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "ONLY_FIRST",
                            "value": "replacement",
                        }
                    ]
                },
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
                f"/api/conversations/{info.id}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "DENIED",
                            "value": "not-allowed",
                        }
                    ]
                },
            )
            assert response.status_code == 403
            allowed = await client.post(
                f"/api/conversations/{info.id}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "ALLOWED",
                            "value": "approved-value",
                        }
                    ]
                },
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
            url = f"/api/conversations/{conversation_id}/configuration/changes"
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {
                                "type": "secret.grant",
                                "name": "TOKEN",
                                "value": "private",
                            }
                        ]
                    },
                )
            ).status_code == 409
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {
                                "type": "secret.grant",
                                "name": "invalid-name",
                                "value": "x",
                            }
                        ]
                    },
                )
            ).status_code == 422
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {"type": "secret.grant", "name": "TOKEN", "value": "  "}
                        ]
                    },
                )
            ).status_code == 422
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {
                                "type": "secret.grant",
                                "name": "TOKEN",
                                "value": "**********",
                            }
                        ]
                    },
                )
            ).status_code == 422
            invalid = await client.post(
                url,
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "TOKEN",
                            "value": "never-reflect-me",
                            "unexpected": True,
                        }
                    ]
                },
            )
            assert invalid.status_code == 422
            assert "never-reflect-me" not in invalid.text
            assert (
                await client.post(
                    url, json={"operations": [{"type": "llm.force", "ref": "x"}]}
                )
            ).status_code == 422
            assert (await client.post(url, json={"operations": []})).status_code == 422
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {"type": "secret.grant", "name": "TOKEN", "value": "x"},
                            {"type": "mcp.attach", "ref": "fetch"},
                        ]
                    },
                )
            ).status_code == 422
        state = await (await service.get_event_service(conversation_id)).get_state()
        assert "TOKEN" not in state.secret_registry.secret_sources


@pytest.mark.asyncio
async def test_attach_saved_mcp_is_live_and_conversation_only(tmp_path):
    cipher = Cipher("mcp-test-key")
    repo = Path(__file__).resolve().parents[2]
    settings_store = get_settings_store(Config(secret_key=SecretStr(cipher.secret_key)))
    settings_store.save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                mcp_config={
                    "fetch": MCPServer(
                        command=sys.executable,
                        args=["-m", "tests.sdk.mcp.stdio_test_server"],
                        cwd=str(repo),
                    )
                }
            )
        )
    )
    root = tmp_path / "conversations"
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        first = await _start(service, tmp_path / "first")
        second = await _start(service, tmp_path / "second")
        async with _client(service, cipher=cipher) as client:
            url = f"/api/conversations/{first}/configuration/changes"
            response = await client.post(
                url, json={"operations": [{"type": "mcp.attach", "ref": "fetch"}]}
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "applied": [{"type": "mcp.attach", "ref": "fetch"}],
            }
            assert (
                await client.post(
                    url, json={"operations": [{"type": "mcp.attach", "ref": "fetch"}]}
                )
            ).status_code == 409
            assert (
                await client.post(
                    url, json={"operations": [{"type": "mcp.attach", "ref": "missing"}]}
                )
            ).status_code == 404
        first_service = await service.get_event_service(first)
        assert "fetch_fetch" in first_service.get_conversation().agent.tools_map
        first_state = await first_service.get_state()
        second_state = await (await service.get_event_service(second)).get_state()
        assert "fetch" in first_state.agent.mcp_config
        assert not second_state.agent.mcp_config
        assert "fetch" in settings_store.load().agent_settings.mcp_config
        fork = await service.fork_conversation(first)
        fork_state = await (await service.get_event_service(fork.id)).get_state()
        assert not fork_state.agent.mcp_config
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        resumed = await service.get_event_service(first)
        assert "fetch" in (await resumed.get_state()).agent.mcp_config
        resumed.get_conversation()._ensure_agent_ready()
        assert "fetch_fetch" in resumed.get_conversation().agent.tools_map


@pytest.mark.asyncio
async def test_unreachable_or_disabled_mcp_does_not_change_conversation(tmp_path):
    cipher = Cipher("mcp-failure-key")
    get_settings_store(Config(secret_key=SecretStr(cipher.secret_key))).save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                mcp_config={
                    "broken": MCPServer(
                        command=sys.executable,
                        args=["-c", "import sys; sys.exit(2)"],
                    ),
                    "disabled": MCPServer(command=sys.executable, enabled=False),
                }
            )
        )
    )
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", cipher=cipher
    ) as service:
        conversation_id = await _start(service, tmp_path / "workspace")
        async with _client(service, cipher=cipher) as client:
            url = f"/api/conversations/{conversation_id}/configuration/changes"
            assert (
                await client.post(
                    url,
                    json={"operations": [{"type": "mcp.attach", "ref": "disabled"}]},
                )
            ).status_code == 422
            response = await client.post(
                url, json={"operations": [{"type": "mcp.attach", "ref": "broken"}]}
            )
            assert response.status_code in (409, 422), response.text
        event_service = await service.get_event_service(conversation_id)
        state = await event_service.get_state()
        assert not state.agent.mcp_config
        assert "broken" not in event_service.get_conversation().agent.tools_map


@pytest.mark.asyncio
async def test_mcp_policy_is_captured_at_launch_not_from_edited_profile(tmp_path):
    cipher = Cipher("mcp-policy-key")
    get_settings_store(Config(secret_key=SecretStr(cipher.secret_key))).save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                mcp_config={"fetch": MCPServer(command=sys.executable)}
            )
        )
    )
    get_llm_profile_store().save(
        "default",
        LLM(model="gpt-4o", api_key=SecretStr("test-key")),
        include_secrets=True,
        cipher=cipher,
    )
    profile = OpenHandsAgentProfile(
        name="no-mcp", llm_profile_ref="default", mcp_server_refs=[], tools=[]
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
        get_agent_profile_store().save(
            profile.model_copy(update={"mcp_server_refs": ["fetch"]})
        )
        async with _client(service, cipher=cipher) as client:
            response = await client.post(
                f"/api/conversations/{info.id}/configuration/changes",
                json={"operations": [{"type": "mcp.attach", "ref": "fetch"}]},
            )
            assert response.status_code == 403, response.text
            event_service = await service.get_event_service(info.id)
            snapshot = event_service.stored.launched_agent_profile
            legacy = LaunchedAgentProfile.model_validate(
                snapshot.model_dump(exclude={"mcp_server_refs"})
            )
            assert legacy.mcp_server_refs == []
            assert (
                LaunchedAgentProfile.model_validate(
                    legacy.model_dump(mode="json")
                ).mcp_server_refs
                == []
            )
            event_service.stored.launched_agent_profile = legacy
            response = await client.post(
                f"/api/conversations/{info.id}/configuration/changes",
                json={"operations": [{"type": "mcp.attach", "ref": "fetch"}]},
            )
            assert response.status_code == 403, response.text
            event_service.stored.launched_agent_profile = snapshot
        assert not (
            await (await service.get_event_service(info.id)).get_state()
        ).agent.mcp_config
