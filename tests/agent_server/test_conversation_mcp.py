"""Selected MCP tools use the conversation REST API's schema and auth."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_llm_profile_store,
)
from openhands.sdk import LLM, TextContent
from openhands.sdk.conversation.request import (
    SendMessageRequest,
    StartConversationRequest,
)
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.workspace import LocalWorkspace


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
ACCEPT = "application/json, text/event-stream"


@pytest.mark.asyncio
async def test_mcp_exposes_selected_rest_schema_without_custom_contract(tmp_path):
    app = create_app(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            session_api_keys=[],
            static_files_path=None,
        )
    )
    async with Client(app.state.conversation_mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {"start_conversation", "get_conversation", "send_message"}
        assert "workspace" in tools["start_conversation"].input_schema["properties"]
        assert (
            "agent_profile_id" in tools["start_conversation"].input_schema["properties"]
        )
        assert "conversation_id" in tools["get_conversation"].input_schema["properties"]
        assert "content" in tools["send_message"].input_schema["properties"]


def _rpc(client: TestClient, method: str, params: dict, key: str | None = None):
    headers = {"accept": ACCEPT}
    if key:
        headers["X-Session-API-Key"] = key
    return client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
        headers=headers,
    )


def test_mcp_uses_same_optional_session_key_rule_as_rest(tmp_path):
    app = create_app(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            session_api_keys=["local-test-key"],
            static_files_path=None,
        )
    )
    with TestClient(app) as client:
        assert _rpc(client, "tools/list", {}).status_code == 401
        assert (
            client.post(
                "/mcp/",
                json=INITIALIZE,
                headers={"accept": ACCEPT, "Authorization": "Bearer local-test-key"},
            ).status_code
            == 401
        )
        assert _rpc(client, "tools/list", {}, key="wrong-key").status_code == 401
        assert _rpc(client, "tools/list", {}, key="local-test-key").status_code == 200
        app.state.config = app.state.config.model_copy(
            update={"session_api_keys": ["rotated-key"]}
        )
        assert _rpc(client, "tools/list", {}, key="local-test-key").status_code == 401
        assert _rpc(client, "tools/list", {}, key="rotated-key").status_code == 200
        app.state.config = app.state.config.model_copy(update={"session_api_keys": []})
        assert _rpc(client, "tools/list", {}).status_code == 200


def test_mcp_waits_for_deferred_init_like_rest(tmp_path):
    app = create_app(
        Config(
            deferred_init=True,
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            session_api_keys=["future-key"],
            static_files_path=None,
        )
    )
    with TestClient(app) as client:
        rest = client.get("/api/conversations/00000000-0000-0000-0000-000000000000")
        assert rest.status_code == 503
        assert _rpc(client, "tools/list", {}).status_code == 503


def test_mcp_calls_existing_rest_create_get_and_message_endpoints(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=workspace,
            session_api_keys=["local-test-key"],
            static_files_path=None,
        )
    )
    get_llm_profile_store().save(
        "mcp-rest-llm", LLM(model="openai/gpt-4o", api_key=SecretStr("unused"))
    )
    profile = OpenHandsAgentProfile(
        name="mcp-rest-agent", llm_profile_ref="mcp-rest-llm", tools=[]
    )
    get_agent_profile_store().save(profile)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir=workspace),
    )
    with TestClient(app) as client:
        created = _rpc(
            client,
            "tools/call",
            {
                "name": "start_conversation",
                "arguments": request.model_dump(mode="json", exclude_none=True),
            },
            key="local-test-key",
        )
        assert created.status_code == 200
        assert not created.json()["result"].get("isError"), created.text
        conversation = created.json()["result"]["structuredContent"]
        conversation_id = conversation["id"]
        assert UUID(conversation_id)
        rest = client.get(
            f"/api/conversations/{conversation_id}",
            headers={"X-Session-API-Key": "local-test-key"},
        )
        assert rest.status_code == 200
        retrieved = _rpc(
            client,
            "tools/call",
            {
                "name": "get_conversation",
                "arguments": {"conversation_id": conversation_id},
            },
            key="local-test-key",
        )
        assert retrieved.status_code == 200
        assert (
            retrieved.json()["result"]["structuredContent"]["id"] == rest.json()["id"]
        )
        message = SendMessageRequest(
            role="user", content=[TextContent(text="Follow-up")], run=False
        )
        sent = _rpc(
            client,
            "tools/call",
            {
                "name": "send_message",
                "arguments": {
                    "conversation_id": conversation_id,
                    **message.model_dump(mode="json"),
                },
            },
            key="local-test-key",
        )
        assert sent.status_code == 200
        assert not sent.json()["result"].get("isError"), sent.text
        assert sent.json()["result"]["structuredContent"] == {"success": True}
        events = client.get(
            f"/api/conversations/{conversation_id}/events/search",
            headers={"X-Session-API-Key": "local-test-key"},
        )
        assert events.status_code == 200
        assert "Follow-up" in events.text
