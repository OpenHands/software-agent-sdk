"""The local MCP endpoint uses the agent server's existing session-key boundary."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.testing import TestLLM
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


def _structured(result: CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


async def _wait_for(
    client: Client, handle: str, key: str, expected: str
) -> dict[str, Any]:
    for _ in range(60):
        result = await client.call_tool("get_conversation", {"handle": handle})
        content = _structured(result)
        if content[key] == expected:
            return content
        await asyncio.sleep(0.1)
    raise AssertionError(f"Conversation did not reach {key}={expected}")


def test_mcp_requires_local_session_key(tmp_path):
    app = create_app(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            session_api_keys=["local-test-key"],
            static_files_path=None,
        )
    )
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer wrong-key"}):
            response = client.post(
                "/mcp/", json=INITIALIZE, headers={"accept": ACCEPT, **headers}
            )
            assert response.status_code == 401
        response = client.post(
            "/mcp/",
            json=INITIALIZE,
            headers={"accept": ACCEPT, "Authorization": "Bearer local-test-key"},
        )
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "OpenHands"
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        assert (
            client.post("/mcp/", json=listing, headers={"accept": ACCEPT}).status_code
            == 401
        )
        listed = client.post(
            "/mcp/",
            json=listing,
            headers={"accept": ACCEPT, "Authorization": "Bearer local-test-key"},
        )
        assert listed.status_code == 200
        assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
            "create_conversation",
            "get_conversation",
            "send_message",
        }
        app.state.config = app.state.config.model_copy(
            update={"session_api_keys": ["rotated-key"]}
        )
        assert (
            client.post(
                "/mcp/",
                json=INITIALIZE,
                headers={"accept": ACCEPT, "Authorization": "Bearer local-test-key"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/mcp/",
                json=INITIALIZE,
                headers={"accept": ACCEPT, "X-Session-API-Key": "rotated-key"},
            ).status_code
            == 200
        )
        app.state.config = app.state.config.model_copy(update={"session_api_keys": []})
        assert (
            client.post(
                "/mcp/", json=INITIALIZE, headers={"accept": ACCEPT}
            ).status_code
            == 503
        )


@pytest.mark.asyncio
async def test_mcp_advertises_only_conversation_tools(tmp_path):
    app = create_app(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            session_api_keys=[],
            static_files_path=None,
        )
    )
    async with Client(app.state.conversation_mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {
            "create_conversation",
            "get_conversation",
            "send_message",
        }
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint == (tool.name == "get_conversation")
        invalid = await client.call_tool(
            "get_conversation", {"handle": "not-a-uuid"}, raise_on_error=False
        )
        assert invalid.is_error is True
        assert "not-a-uuid" not in str(invalid.content)
        empty = await client.call_tool(
            "create_conversation", {"prompt": ""}, raise_on_error=False
        )
        assert empty.is_error is True


@pytest.mark.asyncio
async def test_mcp_followup_uses_existing_conversation_and_safe_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=workspace,
            web_url="http://localhost:3001",
            session_api_keys=["local-test-key"],
            static_files_path=None,
        )
    )
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        app.state.conversation_service = service
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent=Agent(
                    llm=LLM(model="openai/gpt-4o", api_key=SecretStr("unused")),
                    tools=[],
                ),
                workspace=LocalWorkspace(working_dir=workspace),
                confirmation_policy=AlwaysConfirm(),
            )
        )
        events = await service.get_event_service(info.id)
        assert events is not None
        assert events._conversation is not None
        events._conversation.switch_llm(
            TestLLM.from_messages(
                [
                    Message(role="assistant", content=[TextContent(text="Done!")]),
                    Message(role="assistant", content=[TextContent(text="Done!")]),
                    Message(role="assistant", content=[TextContent(text="Updated!")]),
                    Message(role="assistant", content=[TextContent(text="Updated!")]),
                ]
            )
        )
        async with Client(app.state.conversation_mcp) as client:
            handle = str(info.id)
            initial = await client.call_tool("get_conversation", {"handle": handle})
            assert initial.structured_content == {
                "handle": handle,
                "state": "starting",
                "response": None,
                "url": f"http://localhost:3001/conversations/{info.id}",
            }
            accepted = await client.call_tool(
                "send_message", {"handle": handle, "message": "Complete the task."}
            )
            assert accepted.structured_content == {"accepted": True}
            status = await _wait_for(client, handle, "state", "finished")
            assert status["response"] == "Done!"
            assert UUID(status["handle"]) == info.id
            assert set(status) == {
                "handle",
                "state",
                "response",
                "url",
            }
            await client.call_tool(
                "send_message", {"handle": handle, "message": "Update your answer."}
            )
            status = await _wait_for(client, handle, "response", "Updated!")
            assert status["state"] == "finished"


@pytest.mark.asyncio
async def test_mcp_creates_profile_backed_conversation(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=workspace,
        session_api_keys=["local-test-key"],
        static_files_path=None,
    )
    app = create_app(config)
    get_llm_profile_store().save(
        "mcp-llm", LLM(model="openai/gpt-4o", api_key=SecretStr("unused"))
    )
    profile = OpenHandsAgentProfile(
        name="mcp-agent", llm_profile_ref="mcp-llm", tools=[]
    )
    get_agent_profile_store().save(profile)
    get_settings_store(config).save(
        PersistedSettings(active_agent_profile_id=str(profile.id))
    )
    scripted = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="Created!")]),
            Message(role="assistant", content=[TextContent(text="Created!")]),
        ]
    )

    async def complete_locally(self, *args, **kwargs):
        return await scripted.acompletion(*args, **kwargs)

    # The provider call is the only external boundary; service and MCP are real.
    monkeypatch.setattr(LLM, "acompletion", complete_locally)
    async with ConversationService(
        conversations_dir=config.conversations_path
    ) as service:
        app.state.conversation_service = service
        async with Client(app.state.conversation_mcp) as client:
            started = await client.call_tool(
                "create_conversation", {"prompt": "Make a small change."}
            )
            handle = _structured(started)["handle"]
            conversation = await service.get_conversation(UUID(handle))
            assert conversation is not None
            assert (
                conversation.confirmation_policy.__class__.__name__ == "AlwaysConfirm"
            )
            status = await _wait_for(client, handle, "state", "finished")
            assert status["response"] == "Created!"


@pytest.mark.asyncio
async def test_mcp_rejects_followup_without_action_approval(tmp_path):
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
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        app.state.conversation_service = service
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent=Agent(
                    llm=LLM(model="openai/gpt-4o", api_key=SecretStr("unused")),
                    tools=[],
                ),
                workspace=LocalWorkspace(working_dir=workspace),
                confirmation_policy=NeverConfirm(),
            )
        )
        async with Client(app.state.conversation_mcp) as client:
            rejected = await client.call_tool(
                "send_message",
                {"handle": str(info.id), "message": "Run code without approval"},
                raise_on_error=False,
            )
            assert rejected.is_error is True
            assert "Run code without approval" not in str(rejected.content)
            conversation = await service.get_conversation(info.id)
            assert conversation is not None
            assert conversation.execution_status.value == "idle"
