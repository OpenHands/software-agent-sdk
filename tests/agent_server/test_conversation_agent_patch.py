"""Conversation-only profile edits use real state, persistence, and agent code."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from openhands.agent_server.api import _add_exception_handlers
from openhands.agent_server.conversation_agent_patch import (
    apply_conversation_agent_patch,
)
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    StartConversationRequest,
    UpdateConversationAgentRequest,
)
from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_llm_profile_store,
    reset_stores,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.launch import resolve
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import OpenHandsAgentProfile
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace
from tests.sdk.launch import fakes


@pytest.fixture
def conversation(tmp_path: Path):
    cipher = Cipher("test-encryption-key")
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("llm-secret")),
            tools=[],
            include_default_tools=[],
        ),
        workspace=tmp_path / "workspace",
        persistence_dir=tmp_path / "conversations",
        cipher=cipher,
        visualizer=None,
        delete_on_close=False,
    )
    conversation.state.conversation_profile = OpenHandsAgentProfile(
        name="private", llm_profile_ref="default", mcp_server_refs=[]
    )
    try:
        yield conversation
    finally:
        conversation.close()


def test_patch_persists_profile_revision_and_private_credentials(conversation):
    server = MCPServer(
        command="echo", enabled=False, env={"API": SecretStr("mcp-secret")}
    )
    request = UpdateConversationAgentRequest.model_validate(
        {
            "profile_diff": {
                "mcp_server_refs": ["private"],
                "secret_refs": ["NEW_SECRET"],
                "condenser": {"condenser_kind": "no_op"},
            },
            "resources": {
                "mcp_servers": {"private": server},
                "secrets": {"NEW_SECRET": "conversation-secret"},
            },
        }
    )
    result = apply_conversation_agent_patch(conversation, request)
    assert result.conversation_profile_revision == 1
    assert result.conversation_profile.mcp_server_refs == ["private"]
    assert conversation.agent.mcp_config["private"].enabled is False
    assert conversation.state.secret_registry.get_secret_value("NEW_SECRET") == (
        "conversation-secret"
    )

    state_file = Path(conversation.state.persistence_dir) / "base_state.json"
    persisted = state_file.read_text()
    assert "conversation-secret" not in persisted
    assert "mcp-secret" not in persisted
    assert "llm-secret" not in persisted
    assert "NEW_SECRET" in persisted
    resumed = LocalConversation(
        agent=None,
        workspace=conversation.workspace,
        persistence_dir=Path(conversation.state.persistence_dir).parent,
        conversation_id=conversation.state.id,
        cipher=Cipher("test-encryption-key"),
        visualizer=None,
        delete_on_close=False,
    )
    try:
        assert resumed.state.conversation_profile_revision == 1
        assert resumed.state.conversation_profile == result.conversation_profile
        assert resumed.state.secret_registry.get_secret_value("NEW_SECRET") == (
            "conversation-secret"
        )
        env = resumed.state.conversation_mcp_servers["private"].env
        assert env is not None
        assert env["API"].get_secret_value() == "mcp-secret"
    finally:
        resumed.close()


def test_patch_adds_mcp_connection_to_gateway_without_changing_prompt(conversation):
    conversation._ensure_agent_ready()
    assert "list_mcp_tools" not in conversation.agent.tools_map
    prompt_events = list(conversation.state.events)
    request = UpdateConversationAgentRequest.model_validate(
        {
            "profile_diff": {"mcp_server_refs": ["docs"]},
            "resources": {
                "mcp_servers": {
                    "docs": {
                        "command": sys.executable,
                        "args": ["-m", "tests.sdk.mcp.stdio_test_server"],
                        "cwd": str(Path(__file__).resolve().parents[2]),
                    }
                }
            },
        }
    )
    result = apply_conversation_agent_patch(conversation, request)
    assert result.conversation_profile_revision == 1
    assert conversation.agent is conversation.state.agent
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
    assert [
        tool["name"] for tool in conversation.mcp_gateway.list_tools()["tools"]
    ] == ["docs.fetch"]
    assert list(conversation.state.events) == prompt_events

    resumed = LocalConversation(
        agent=None,
        workspace=conversation.workspace,
        persistence_dir=Path(conversation.state.persistence_dir).parent,
        conversation_id=conversation.state.id,
        cipher=Cipher("test-encryption-key"),
        visualizer=None,
        delete_on_close=False,
    )
    try:
        assert resumed.state.conversation_profile_revision == 1
        assert [tool["name"] for tool in resumed.mcp_gateway.list_tools()["tools"]] == [
            "docs.fetch"
        ]
    finally:
        resumed.close()


def test_profile_addition_never_removes_existing_connection(conversation):
    server = MCPServer(
        command=sys.executable,
        args=["-m", "tests.sdk.mcp.stdio_test_server"],
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    conversation._ensure_agent_ready()
    first = UpdateConversationAgentRequest.model_validate(
        {
            "profile_diff": {"mcp_server_refs": ["docs"]},
            "resources": {"mcp_servers": {"docs": server}},
        }
    )
    apply_conversation_agent_patch(conversation, first)
    client = conversation.mcp_gateway.clients["docs"]
    second = UpdateConversationAgentRequest.model_validate(
        {
            "profile_diff": {"mcp_server_refs": ["docs", "other"]},
            "resources": {"mcp_servers": {"other": server}},
        }
    )
    result = apply_conversation_agent_patch(conversation, second)
    assert result.conversation_profile.mcp_server_refs == ["docs", "other"]
    assert conversation.mcp_gateway.clients["docs"] is client
    assert not client._closed
    assert {
        tool["name"] for tool in conversation.mcp_gateway.list_tools()["tools"]
    } == {"docs.fetch", "other.fetch"}

    for request in (
        UpdateConversationAgentRequest(profile_diff={"mcp_server_refs": ["other"]}),
        UpdateConversationAgentRequest.model_validate(
            {"resources": {"mcp_servers": {"docs": {"command": "echo"}}}}
        ),
    ):
        with pytest.raises(ValueError, match="remove|already exists"):
            apply_conversation_agent_patch(conversation, request)
    assert conversation.state.conversation_profile_revision == 2
    assert conversation.mcp_gateway.clients["docs"] is client
    assert not client._closed


def test_patch_rejects_prompt_and_identity_changes_without_mutation(conversation):
    for diff in (
        {"persona": "new system prompt"},
        {"system_prompt": "new system prompt"},
        {"system_message_suffix": "new system prompt"},
        {"name": "global"},
        {"id": "random"},
    ):
        with pytest.raises(ValueError, match="Unsupported conversation profile"):
            apply_conversation_agent_patch(
                conversation, UpdateConversationAgentRequest(profile_diff=diff)
            )
    assert conversation.state.conversation_profile_revision == 0
    assert conversation.state.conversation_profile.name == "private"


def test_secret_allowlist_and_unknown_mcp_ref_rejected(conversation):
    with pytest.raises(ValueError, match="Secret names not allowed"):
        apply_conversation_agent_patch(
            conversation,
            UpdateConversationAgentRequest.model_validate(
                {
                    "profile_diff": {"secret_refs": []},
                    "resources": {"secrets": {"UNLISTED": "sensitive"}},
                }
            ),
        )
    with pytest.raises(ValueError, match="Unknown MCP server"):
        apply_conversation_agent_patch(
            conversation,
            UpdateConversationAgentRequest(
                profile_diff={"mcp_server_refs": ["absent"]}
            ),
        )
    assert conversation.state.conversation_profile_revision == 0
    assert not conversation.state.secret_registry.secret_sources


def test_reducing_secret_scope_removes_live_credential(conversation):
    apply_conversation_agent_patch(
        conversation,
        UpdateConversationAgentRequest.model_validate(
            {"resources": {"secrets": {"TOKEN": "sensitive"}}}
        ),
    )
    assert conversation.state.secret_registry.get_secret_value("TOKEN") == "sensitive"
    result = apply_conversation_agent_patch(
        conversation, UpdateConversationAgentRequest(profile_diff={"secret_refs": []})
    )
    assert result.conversation_profile_revision == 2
    assert conversation.state.secret_registry.get_secret_value("TOKEN") is None


@pytest.mark.asyncio
async def test_service_applies_patch_and_restores_revision_after_restart(tmp_path):
    conversations_dir = tmp_path / "service_conversations"
    workspace = LocalWorkspace(working_dir=tmp_path / "service_workspace")
    async with ConversationService(
        conversations_dir=conversations_dir, cipher=Cipher("service-encryption-key")
    ) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent=Agent(
                    llm=LLM(model="test-model"), tools=[], include_default_tools=[]
                ),
                workspace=workspace,
            )
        )
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        event_service.get_conversation().state.conversation_profile = (
            OpenHandsAgentProfile(
                name="conversation", llm_profile_ref="default", mcp_server_refs=[]
            )
        )
        result = await event_service.update_agent_profile(
            UpdateConversationAgentRequest.model_validate(
                {
                    "profile_diff": {"secret_refs": ["TEST_SECRET"]},
                    "resources": {"secrets": {"TEST_SECRET": "sensitive"}},
                }
            )
        )
        assert result.conversation_profile_revision == 1
        assert event_service.stored.launched_agent_profile is None

    async with ConversationService(
        conversations_dir=conversations_dir, cipher=Cipher("service-encryption-key")
    ) as restarted:
        info = await restarted.get_conversation(info.id)
        assert info is not None
        assert info.conversation_profile_revision == 1
        assert info.conversation_profile is not None
        assert info.conversation_profile.secret_refs == ["TEST_SECRET"]
        event_service = await restarted.get_event_service(info.id)
        assert event_service is not None
        assert (
            event_service.get_conversation().state.secret_registry.get_secret_value(
                "TEST_SECRET"
            )
            == "sensitive"
        )


def test_profile_resolution_records_immutable_launch_snapshot():
    profile = OpenHandsAgentProfile(name="saved", llm_profile_ref="default")
    stores = fakes.stores(profile)
    launched = resolve(profile.id, stores)
    assert launched.profile is not None
    assert launched.profile.profile_snapshot == profile
    assert launched.profile.profile_snapshot is not None
    profile.name = "mutated globally"
    assert launched.profile.profile_snapshot.name == "saved"


@pytest.mark.asyncio
async def test_saved_profile_launch_is_snapshot_and_edits_are_local(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "settings"))
    reset_stores()
    try:
        get_llm_profile_store().save("default", LLM(model="test-model"))
        profile = OpenHandsAgentProfile(
            name="saved", llm_profile_ref="default", tools=[], mcp_server_refs=[]
        )
        get_agent_profile_store().save(profile)
        conversations_dir = tmp_path / "saved_conversations"
        cipher = Cipher("saved-profile-test-key")
        async with ConversationService(
            conversations_dir=conversations_dir, cipher=cipher
        ) as service:
            info, _ = await service.start_conversation(
                StartConversationRequest(
                    agent_profile_id=profile.id,
                    workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
                )
            )
            assert info.conversation_profile == profile
            assert info.launched_agent_profile is not None
            assert info.launched_agent_profile.profile_snapshot == profile
            event_service = await service.get_event_service(info.id)
            assert event_service is not None
            result = await event_service.update_agent_profile(
                UpdateConversationAgentRequest(profile_diff={"secret_refs": []})
            )
            assert result.conversation_profile_revision == 1
            saved = get_agent_profile_store().load(profile.name)
            assert saved is not None
            assert saved.secret_refs is None
        async with ConversationService(
            conversations_dir=conversations_dir, cipher=cipher
        ) as restarted:
            restored = await restarted.get_conversation(info.id)
            assert restored is not None
            assert restored.conversation_profile is not None
            assert restored.launched_agent_profile is not None
            assert restored.launched_agent_profile.profile_snapshot is not None
            assert restored.conversation_profile.secret_refs == []
            assert restored.launched_agent_profile.profile_snapshot.secret_refs is None
    finally:
        reset_stores()


def test_invalid_inline_mcp_request_does_not_echo_credentials():
    app = FastAPI()
    _add_exception_handlers(app)

    @app.patch("/api/conversations/{conversation_id}/agent")
    def update(conversation_id: str, request: UpdateConversationAgentRequest) -> dict:
        return {"conversation_id": conversation_id}

    with TestClient(app) as client:
        response = client.patch(
            "/api/conversations/example/agent",
            json={
                "resources": {
                    "mcp_servers": {
                        "local": {
                            "command": "",
                            "env": {"UNUSUAL_NAME": "private-value"},
                        }
                    }
                }
            },
        )
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.asyncio
async def test_patch_endpoint_edits_only_its_conversation(tmp_path):
    cipher = Cipher("api-patch-test-key")
    async with ConversationService(
        conversations_dir=tmp_path / "api_conversations", cipher=cipher
    ) as service:
        agent = Agent(llm=LLM(model="test-model"), tools=[], include_default_tools=[])
        workspace = LocalWorkspace(working_dir=tmp_path / "api_workspace")
        first, _ = await service.start_conversation(
            StartConversationRequest(agent=agent, workspace=workspace)
        )
        second, _ = await service.start_conversation(
            StartConversationRequest(agent=agent, workspace=workspace)
        )
        for info in (first, second):
            events = await service.get_event_service(info.id)
            assert events is not None
            events.get_conversation().state.conversation_profile = (
                OpenHandsAgentProfile(
                    name="private", llm_profile_ref="default", mcp_server_refs=[]
                )
            )
        app = FastAPI()
        app.include_router(conversation_router, prefix="/api")
        _add_exception_handlers(app)
        app.dependency_overrides[get_conversation_service] = lambda: service
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            endpoint = f"/api/conversations/{first.id}/agent"
            denied = await client.patch(
                endpoint, json={"profile_diff": {"persona": "override"}}
            )
            assert denied.status_code == 400
            response = await client.patch(
                endpoint,
                json={
                    "profile_diff": {"secret_refs": ["PRIVATE"]},
                    "resources": {"secrets": {"PRIVATE": "opaque-value"}},
                },
            )
            assert response.status_code == 200
            assert response.json()["conversation_profile_revision"] == 1
            assert "opaque-value" not in response.text
        changed = await service.get_conversation(first.id)
        untouched = await service.get_conversation(second.id)
        assert changed is not None and changed.conversation_profile is not None
        assert untouched is not None and untouched.conversation_profile is not None
        assert changed.conversation_profile.secret_refs == ["PRIVATE"]
        assert untouched.conversation_profile.secret_refs is None
        assert untouched.conversation_profile_revision == 0
        events = await service.get_event_service(first.id)
        assert events is not None
        await events.update_secrets({"PRIVATE": "updated", "DENIED": "blocked"})
        sources = events.get_conversation().state.secret_registry
        assert sources.get_secret_value("PRIVATE") == "updated"
        assert "DENIED" not in sources.secret_sources


@pytest.mark.asyncio
async def test_secret_bearing_edit_requires_persistence_cipher(tmp_path):
    async with ConversationService(
        conversations_dir=tmp_path / "unencrypted"
    ) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent=Agent(llm=LLM(model="test-model"), tools=[]),
                workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
            )
        )
        events = await service.get_event_service(info.id)
        assert events is not None
        events.get_conversation().state.conversation_profile = OpenHandsAgentProfile(
            name="private", llm_profile_ref="default", mcp_server_refs=[]
        )
        request = UpdateConversationAgentRequest.model_validate(
            {"resources": {"secrets": {"PRIVATE": "opaque-value"}}}
        )
        with pytest.raises(ValueError, match="persistence cipher"):
            await events.update_agent_profile(request)
        assert events.get_conversation().state.conversation_profile_revision == 0
