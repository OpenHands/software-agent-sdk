"""Conversation configuration additions use the real API and resume path."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.openapi import build_public_openapi
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
    OpenHandsAgentProfile,
)
from openhands.sdk.secret import StaticSecret
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


async def _event_service(
    service: ConversationService, conversation_id: UUID
) -> EventService:
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    return event_service


async def _start(service: ConversationService, workspace_dir):
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
            workspace=LocalWorkspace(working_dir=workspace_dir),
        )
    )
    return info.id


def test_conversation_configuration_openapi_references_resolve():
    schema = build_public_openapi()
    request_schema = schema["paths"][
        "/api/conversations/{conversation_id}/configuration/changes"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]

    def check_references(value):
        if isinstance(value, dict):
            if ref := value.get("$ref"):
                target = schema
                for key in ref.removeprefix("#/").split("/"):
                    target = target[key.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                check_references(child)
        elif isinstance(value, list):
            for child in value:
                check_references(child)

    check_references(request_schema)


@pytest.mark.asyncio
async def test_reconfigure_agent_uses_one_server_entrypoint_for_fields_and_secrets(
    tmp_path: Path,
) -> None:
    cipher = Cipher("conversation-test-key")
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", cipher=cipher
    ) as service:
        conversation_id = await _start(service, tmp_path / "workspace")
        event_service = await _event_service(service, conversation_id)
        await event_service.reconfigure_agent({"tool_concurrency_limit": 2})
        await event_service.reconfigure_agent(secret=("PRIVATE_KEY", "private-value"))
        state = await event_service.get_state()
        assert state.agent.tool_concurrency_limit == 2
        assert state.secret_registry.get_secret_value("PRIVATE_KEY") == "private-value"
        assert "PRIVATE_KEY" in event_service.stored.configuration_overrides.secret_refs
        with pytest.raises(ValueError, match="one configuration operation"):
            await event_service.reconfigure_agent(
                {"tool_concurrency_limit": 3}, secret=("OTHER", "private")
            )
        assert (await event_service.get_state()).agent.tool_concurrency_limit == 2
        assert "OTHER" not in event_service.stored.configuration_overrides.secret_refs


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
        first_state = await (await _event_service(service, first)).get_state()
        second_state = await (await _event_service(service, second)).get_state()
        assert first_state.secret_registry.get_secret_value("ONLY_FIRST") == (
            "unique-private-value"
        )
        assert "ONLY_FIRST" not in second_state.secret_registry.secret_sources
        assert get_secrets_store().get_secret("ONLY_FIRST") is None
        fork = await service.fork_conversation(first)
        assert fork is not None
        fork_state = await (await _event_service(service, fork.id)).get_state()
        assert "ONLY_FIRST" not in fork_state.secret_registry.secret_sources
        saved = (root / first.hex / "base_state.json").read_text()
        assert "unique-private-value" not in saved
        assert json.loads(saved)["secret_registry"]["secret_sources"]
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        state = await (await _event_service(service, first)).get_state()
        assert state.secret_registry.get_secret_value("ONLY_FIRST") == (
            "unique-private-value"
        )


@pytest.mark.asyncio
async def test_conversation_secret_overrides_preserve_launch_profile_snapshot(tmp_path):
    cipher = Cipher("conversation-test-key")
    profile = ACPAgentProfile(
        name="restricted", acp_server="codex", secret_refs=["ALLOWED"]
    )
    get_agent_profile_store().save(profile)
    root = tmp_path / "conversations"
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
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
                            "name": "ONLY_THIS",
                            "value": "private-value",
                            "scope": "conversation",
                        }
                    ]
                },
            )
            assert response.status_code == 200, response.text
            concurrent = await asyncio.gather(
                *(
                    client.post(
                        f"/api/conversations/{info.id}/configuration/changes",
                        json={
                            "operations": [
                                {"type": "secret.grant", "name": name, "value": name}
                            ]
                        },
                    )
                    for name in ("ONE", "TWO")
                )
            )
            assert all(result.status_code == 200 for result in concurrent)
            details = await client.get(f"/api/conversations/{info.id}")
            assert details.status_code == 200, details.text
            assert details.json()["launched_agent_profile"]["secret_refs"] == [
                "ALLOWED"
            ]
            assert details.json()["configuration_overrides"]["secret_refs"] == [
                "ONLY_THIS",
                "ONE",
                "TWO",
            ]
        event_service = await _event_service(service, info.id)
        assert event_service.stored.launched_agent_profile.secret_refs == ["ALLOWED"]
        overrides = event_service.stored.configuration_overrides
        assert overrides.schema_version == 1
        assert overrides.revision == 3
        assert overrides.secret_refs == ["ONLY_THIS", "ONE", "TWO"]
        assert overrides.mcp_server_refs == []
        info = await service.get_conversation(info.id)
        assert info.launched_agent_profile.secret_refs == ["ALLOWED"]
        assert info.configuration_overrides == overrides
        assert (await event_service.get_state()).secret_registry.get_secret_value(
            "ONLY_THIS"
        ) == "private-value"
        await event_service.update_secrets({"ONE": "refreshed", "DENIED": "blocked"})
        live_secrets = (await event_service.get_state()).secret_registry
        assert live_secrets.get_secret_value("ONE") == "refreshed"
        assert "DENIED" not in live_secrets.secret_sources
        assert get_agent_profile_store().load("restricted").secret_refs == ["ALLOWED"]
        get_agent_profile_store().save(
            profile.model_copy(update={"secret_refs": ["NEW_SHARED_GRANT"]})
        )
        await event_service.update_secrets({"NEW_SHARED_GRANT": "not-this-chat"})
        assert (
            "NEW_SHARED_GRANT"
            not in (await event_service.get_state()).secret_registry.secret_sources
        )
        assert get_secrets_store().get_secret("ONLY_THIS") is None
        saved_meta = (root / info.id.hex / "meta.json").read_text()
        assert "ONLY_THIS" in saved_meta and "private-value" not in saved_meta
        fork = await service.fork_conversation(info.id)
        assert fork is not None
        assert fork.launched_agent_profile.secret_refs == ["ALLOWED"]
        assert fork.configuration_overrides.secret_refs == []
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        restored = await _event_service(service, info.id)
        assert restored.stored.launched_agent_profile.secret_refs == ["ALLOWED"]
        assert restored.stored.configuration_overrides.secret_refs == [
            "ONLY_THIS",
            "ONE",
            "TWO",
        ]
        assert (await restored.get_state()).secret_registry.get_secret_value(
            "ONLY_THIS"
        ) == "private-value"
        await restored.update_secrets({"NEW_SHARED_GRANT": "still-not-this-chat"})
        assert (
            "NEW_SHARED_GRANT"
            not in (await restored.get_state()).secret_registry.secret_sources
        )


@pytest.mark.asyncio
async def test_legacy_expanded_profile_snapshot_keeps_effective_grants(tmp_path):
    profile = ACPAgentProfile(name="legacy-grants", acp_server="codex", secret_refs=[])
    get_agent_profile_store().save(profile)
    root = tmp_path / "conversations"
    cipher = Cipher("legacy-grants-key")
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent_profile_id=profile.id,
                workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
            )
        )
        event_service = await _event_service(service, info.id)
        launched = event_service.stored.launched_agent_profile
        assert launched is not None
        await event_service.save_meta(
            event_service.stored.model_copy(
                update={
                    "launched_agent_profile": launched.model_copy(
                        update={"secret_refs": ["OLD_GRANT"]}
                    )
                }
            )
        )
    meta_file = root / info.id.hex / "meta.json"
    legacy_meta = json.loads(meta_file.read_text())
    legacy_meta.pop("configuration_overrides", None)
    meta_file.write_text(json.dumps(legacy_meta))
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        restored = await _event_service(service, info.id)
        assert restored.stored.configuration_overrides.revision == 0
        await restored.update_secrets({"OLD_GRANT": "retained", "UNKNOWN": "denied"})
        registry = (await restored.get_state()).secret_registry
        assert registry.get_secret_value("OLD_GRANT") == "retained"
        assert "UNKNOWN" not in registry.secret_sources


@pytest.mark.asyncio
async def test_profile_secret_scope_saves_vault_and_profile_reference(tmp_path):
    cipher = Cipher("profile-secret-key")
    profile = ACPAgentProfile(name="my-agent", acp_server="codex", secret_refs=[])
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
                            "name": "PERSISTED",
                            "value": "private-profile-value",
                            "scope": "agent_profile",
                        }
                    ]
                },
            )
            assert response.status_code == 200, response.text
        assert get_secrets_store().get_secret("PERSISTED") == "private-profile-value"
        saved_profile = get_agent_profile_store().load("my-agent")
        assert saved_profile.secret_refs == ["PERSISTED"]
        assert saved_profile.id == profile.id
        assert saved_profile.revision > profile.revision
        stored = (await _event_service(service, info.id)).stored
        assert stored.launched_agent_profile.secret_refs == []
        assert stored.configuration_overrides.secret_refs == ["PERSISTED"]
        next_info, _ = await service.start_conversation(
            StartConversationRequest(
                agent_profile_id=profile.id,
                workspace=LocalWorkspace(working_dir=tmp_path / "next-workspace"),
                secrets={
                    "PERSISTED": StaticSecret(
                        value=SecretStr(get_secrets_store().get_secret("PERSISTED"))
                    )
                },
            )
        )
        next_state = await (await _event_service(service, next_info.id)).get_state()
        assert next_state.secret_registry.get_secret_value("PERSISTED") == (
            "private-profile-value"
        )


@pytest.mark.asyncio
async def test_profile_scope_without_saved_profile_never_saves_secret(tmp_path):
    cipher = Cipher("profile-secret-key")
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", cipher=cipher
    ) as service:
        conversation_id = await _start(service, tmp_path / "workspace")
        async with _client(service, cipher=cipher) as client:
            response = await client.post(
                f"/api/conversations/{conversation_id}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "NO_PROFILE",
                            "value": "must-not-persist",
                            "scope": "agent_profile",
                        }
                    ]
                },
            )
            assert response.status_code == 409, response.text
        assert get_secrets_store().get_secret("NO_PROFILE") is None
        state = await (await _event_service(service, conversation_id)).get_state()
        assert "NO_PROFILE" not in state.secret_registry.secret_sources


@pytest.mark.asyncio
async def test_saved_secret_attaches_without_value_or_unintended_profile_update(
    tmp_path,
):
    cipher = Cipher("saved-reference-key")
    vault = get_secrets_store(Config(secret_key=SecretStr(cipher.secret_key)))
    vault.set_secret("EXISTING", "already-stored")
    vault.set_secret("ONE_OFF", "not-for-profile")
    profile = ACPAgentProfile(
        name="saved-ref-agent", acp_server="codex", secret_refs=[]
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
            url = f"/api/conversations/{info.id}/configuration/changes"
            response = await client.post(
                url,
                json={
                    "operations": [
                        {
                            "type": "secret.attach",
                            "name": "EXISTING",
                            "scope": "agent_profile",
                        }
                    ]
                },
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "applied": [{"type": "secret.attach", "name": "EXISTING"}]
            }
            assert "already-stored" not in response.text
            one_off = await client.post(
                url,
                json={"operations": [{"type": "secret.attach", "name": "ONE_OFF"}]},
            )
            assert one_off.status_code == 200, one_off.text
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {
                                "type": "secret.grant",
                                "name": "EXISTING",
                                "value": "replacement",
                                "scope": "agent_profile",
                            }
                        ]
                    },
                )
            ).status_code == 409
        assert get_secrets_store().get_secret("EXISTING") == "already-stored"
        with pytest.raises(FileExistsError):
            get_secrets_store().set_secret("EXISTING", "replacement", create_only=True)
        assert get_secrets_store().get_secret("EXISTING") == "already-stored"
        assert get_agent_profile_store().load("saved-ref-agent").secret_refs == [
            "EXISTING"
        ]
        current = await (await _event_service(service, info.id)).get_state()
        assert current.secret_registry.get_secret_value("EXISTING") == "already-stored"
        assert current.secret_registry.get_secret_value("ONE_OFF") == "not-for-profile"


@pytest.mark.asyncio
async def test_profile_save_failure_reports_conversation_only_partial_success(tmp_path):
    cipher = Cipher("partial-profile-key")
    profile = ACPAgentProfile(name="partial", acp_server="codex", secret_refs=[])
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
        store = get_secrets_store(Config(secret_key=SecretStr(cipher.secret_key)))
        store.persistence_dir.mkdir(parents=True, exist_ok=True)
        store._path.write_text("invalid json")
        async with _client(service, cipher=cipher) as client:
            response = await client.post(
                f"/api/conversations/{info.id}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "secret.grant",
                            "name": "PARTIAL",
                            "value": "not-in-vault",
                            "scope": "agent_profile",
                        }
                    ]
                },
            )
            assert response.status_code == 503, response.text
            assert "conversation" in response.json()["detail"]
            assert "not-in-vault" not in response.text
        assert get_agent_profile_store().load("partial").secret_refs == []
        assert (
            await (await _event_service(service, info.id)).get_state()
        ).secret_registry.get_secret_value("PARTIAL") == "not-in-vault"


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
                                "type": "mcp.install",
                                "name": "private",
                                "server": {
                                    "command": sys.executable,
                                    "env": {"TOKEN": "not-persisted"},
                                },
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
        state = await (await _event_service(service, conversation_id)).get_state()
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
            inventory = (await client.get(f"/api/conversations/{first}/tools")).json()
            assert any(
                tool["name"] == "fetch_fetch"
                and tool["source"] == "conversation"
                and tool["server_name"] == "fetch"
                for tool in inventory["tools"]
            )
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
        first_service = await _event_service(service, first)
        assert "fetch_fetch" in first_service.get_conversation().agent.tools_map
        first_state = await first_service.get_state()
        second_state = await (await _event_service(service, second)).get_state()
        assert "fetch" in first_state.agent.mcp_config
        assert not second_state.agent.mcp_config
        settings_snapshot = settings_store.load()
        assert settings_snapshot is not None
        assert "fetch" in settings_snapshot.agent_settings.mcp_config
        fork = await service.fork_conversation(first)
        assert fork is not None
        fork_state = await (await _event_service(service, fork.id)).get_state()
        assert not fork_state.agent.mcp_config
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        resumed = await _event_service(service, first)
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
        event_service = await _event_service(service, conversation_id)
        state = await event_service.get_state()
        assert not state.agent.mcp_config
        assert "broken" not in event_service.get_conversation().agent.tools_map


@pytest.mark.asyncio
async def test_mcp_explicit_attach_records_conversation_overrides_not_saved_profile(
    tmp_path,
):
    cipher = Cipher("mcp-policy-key")
    repo = Path(__file__).resolve().parents[2]
    server = MCPServer(
        command=sys.executable,
        args=["-m", "tests.sdk.mcp.stdio_test_server"],
        cwd=str(repo),
    )
    get_settings_store(Config(secret_key=SecretStr(cipher.secret_key))).save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(mcp_config={"fetch": server})
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
    root = tmp_path / "conversations"
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent_profile_id=profile.id,
                workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
            )
        )
        async with _client(service, cipher=cipher) as client:
            url = f"/api/conversations/{info.id}/configuration/changes"
            response = await client.post(
                url, json={"operations": [{"type": "mcp.attach", "ref": "fetch"}]}
            )
            assert response.status_code == 200, response.text
            inline = await client.post(
                url,
                json={
                    "operations": [
                        {
                            "type": "mcp.install",
                            "name": "inline",
                            "server": server.model_dump(mode="json"),
                        }
                    ]
                },
            )
            assert inline.status_code == 200, inline.text
            stored = (await _event_service(service, info.id)).stored
            assert stored.launched_agent_profile.mcp_server_refs == []
            assert stored.configuration_overrides.mcp_server_refs == ["fetch", "inline"]
            assert stored.configuration_overrides.revision == 2
            assert get_agent_profile_store().load("no-mcp").mcp_server_refs == []
            assert (
                await client.post(
                    url,
                    json={
                        "operations": [
                            {
                                "type": "mcp.install",
                                "name": "broken",
                                "server": {
                                    "command": sys.executable,
                                    "args": ["-c", "exit(1)"],
                                },
                            }
                        ]
                    },
                )
            ).status_code == 409
            assert "broken" not in stored.configuration_overrides.mcp_server_refs
            assert stored.configuration_overrides.revision == 2
        assert "fetch" in (root / info.id.hex / "meta.json").read_text()
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        restored = (await _event_service(service, info.id)).stored
        assert restored.launched_agent_profile.mcp_server_refs == []
        assert restored.configuration_overrides.mcp_server_refs == ["fetch", "inline"]


@pytest.mark.asyncio
async def test_inline_mcp_install_live_inventory_isolated_encrypted_and_restored(
    tmp_path,
):
    cipher = Cipher("inline-mcp-key")
    root = tmp_path / "conversations"
    repo = Path(__file__).resolve().parents[2]
    server = {
        "command": sys.executable,
        "args": ["-m", "tests.sdk.mcp.stdio_test_server"],
        "cwd": str(repo),
    }
    settings = get_settings_store(Config(secret_key=SecretStr(cipher.secret_key)))
    settings.save(PersistedSettings(agent_settings=OpenHandsAgentSettings()))
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        first, _ = await service.start_conversation(
            StartConversationRequest(
                agent=Agent(
                    llm=LLM(model="gpt-4o", usage_id="test"),
                    tools=[],
                    mcp_config={"base": MCPServer.model_validate(server)},
                ),
                workspace=LocalWorkspace(working_dir=tmp_path / "first"),
            )
        )
        second = await _start(service, tmp_path / "second")
        async with _client(service, cipher=cipher) as client:
            url = f"/api/conversations/{first.id}"
            initial = await client.get(f"{url}/tools")
            assert initial.status_code == 200, initial.text
            assert any(
                t["name"] == "fetch"
                and t["source"] == "global"
                and t["server_name"] == "base"
                for t in initial.json()["tools"]
            )
            response = await client.post(
                f"{url}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "mcp.install",
                            "name": "inline",
                            "server": {
                                **server,
                                "env": {"PRIVATE_TOKEN": "inline-secret-value"},
                            },
                        }
                    ]
                },
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "applied": [{"type": "mcp.install", "name": "inline"}]
            }
            inventory = await client.get(f"{url}/tools")
            assert inventory.status_code == 200, inventory.text
            assert "inline-secret-value" not in inventory.text
            assert "PRIVATE_TOKEN" not in inventory.text
            tools = inventory.json()["tools"]
            assert any(
                t["name"] == "inline_fetch"
                and t["source"] == "conversation"
                and t["server_name"] == "inline"
                and isinstance(t["parameters"], dict)
                for t in tools
            )
            assert any(t["source"] == "builtin" for t in tools)
            assert (
                await client.get(f"/api/conversations/{second}/tools")
            ).status_code == 200
            assert not any(
                t["name"] == "inline_fetch"
                for t in (
                    await client.get(f"/api/conversations/{second}/tools")
                ).json()["tools"]
            )
            assert (
                await client.post(
                    f"{url}/configuration/changes",
                    json={
                        "operations": [
                            {"type": "mcp.install", "name": "inline", "server": server}
                        ]
                    },
                )
            ).status_code == 409
            invalid = await client.post(
                f"{url}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "mcp.install",
                            "name": "other",
                            "server": {
                                **server,
                                "headers": {"TOKEN": "never-reflect-me"},
                                "unknown": True,
                            },
                        }
                    ]
                },
            )
            assert invalid.status_code == 422
            assert "never-reflect-me" not in invalid.text
        settings_snapshot = settings.load()
        assert settings_snapshot is not None
        assert not settings_snapshot.agent_settings.mcp_config
        saved = (root / first.id.hex / "base_state.json").read_text()
        assert "inline-secret-value" not in saved
        fork = await service.fork_conversation(first.id)
        assert fork is not None
        fork_tools = (
            await (await _event_service(service, fork.id)).get_state()
        ).agent.mcp_config
        assert "inline" not in fork_tools
    async with ConversationService(conversations_dir=root, cipher=cipher) as service:
        async with _client(service, cipher=cipher) as client:
            response = await client.get(f"/api/conversations/{first.id}/tools")
            assert response.status_code == 200, response.text
            assert any(
                t["name"] == "inline_fetch" and t["source"] == "conversation"
                for t in response.json()["tools"]
            )
            assert "inline-secret-value" not in response.text


@pytest.mark.asyncio
async def test_inline_mcp_install_failure_rolls_back(tmp_path):
    cipher = Cipher("inline-mcp-failure")
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", cipher=cipher
    ) as service:
        conversation_id = await _start(service, tmp_path / "workspace")
        url = f"/api/conversations/{conversation_id}"
        async with _client(service, cipher=cipher) as client:
            response = await client.post(
                f"{url}/configuration/changes",
                json={
                    "operations": [
                        {
                            "type": "mcp.install",
                            "name": "broken",
                            "server": {
                                "command": sys.executable,
                                "args": ["-c", "import sys; sys.exit(2)"],
                                "env": {"TOKEN": "private-failure"},
                            },
                        }
                    ]
                },
            )
            assert response.status_code == 409, response.text
            assert "private-failure" not in response.text
            assert not any(
                t.get("server_name") == "broken"
                for t in (await client.get(f"{url}/tools")).json()["tools"]
            )
        assert (
            "broken"
            not in (
                await (await _event_service(service, conversation_id)).get_state()
            ).agent.mcp_config
        )


@pytest.mark.asyncio
async def test_concurrent_inline_installs_keep_both_servers(tmp_path):
    cipher = Cipher("inline-concurrent-key")
    repo = Path(__file__).resolve().parents[2]
    server = {
        "command": sys.executable,
        "args": ["-m", "tests.sdk.mcp.stdio_test_server"],
        "cwd": str(repo),
    }
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", cipher=cipher
    ) as service:
        conversation_id = await _start(service, tmp_path / "workspace")
        async with _client(service, cipher=cipher) as client:
            url = f"/api/conversations/{conversation_id}"
            responses = await asyncio.gather(
                *(
                    client.post(
                        f"{url}/configuration/changes",
                        json={
                            "operations": [
                                {"type": "mcp.install", "name": name, "server": server}
                            ]
                        },
                    )
                    for name in ("first", "second")
                )
            )
            assert [response.status_code for response in responses] == [200, 200]
            inventory = (await client.get(f"{url}/tools")).json()["tools"]
            assert {t["name"] for t in inventory if t["source"] == "conversation"} == {
                "first_fetch",
                "second_fetch",
            }
        state = await (await _event_service(service, conversation_id)).get_state()
        assert {"first", "second"} <= set(state.agent.mcp_config)
