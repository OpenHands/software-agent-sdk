"""Tests for the user-facing feature flags and the tools they turn on."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.experimental_features import experimental_feature_tools
from openhands.agent_server.experimental_features_router import (
    experimental_features_router,
)
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk import LLM, Agent
from openhands.sdk.agent import ACPAgent
from openhands.sdk.launch.finalize import LaunchRuntime, finalize
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config(
        static_files_path=None, session_api_keys=[], secret_key=None
    )
    app.include_router(experimental_features_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_features_are_listed_off_by_default(client):
    response = await client.get("/api/settings/experimental")

    assert response.status_code == 200
    assert response.json() == [
        {
            "key": "conversation_reactions",
            "label": "Conversation reactions",
            "description": response.json()[0]["description"],
            "default": False,
            "enabled": False,
        }
    ]


@pytest.mark.asyncio
async def test_turning_a_feature_on_and_off_is_saved(client):
    on = await client.patch(
        "/api/settings/experimental/conversation_reactions", json={"enabled": True}
    )
    listed_on = await client.get("/api/settings/experimental")
    off = await client.patch(
        "/api/settings/experimental/conversation_reactions", json={"enabled": False}
    )

    assert on.status_code == 200
    assert on.json()["enabled"] is True
    assert listed_on.json()[0]["enabled"] is True
    assert off.json()["enabled"] is False
    stored = get_settings_store().load()
    assert stored is not None
    assert stored.experimental_features == {"conversation_reactions": False}


@pytest.mark.asyncio
async def test_unknown_features_are_refused(client):
    response = await client.patch(
        "/api/settings/experimental/time_travel", json={"enabled": True}
    )

    assert response.status_code == 404
    assert (
        get_settings_store().load() or PersistedSettings()
    ).experimental_features == {}


def test_feature_tools_follow_the_flags():
    assert experimental_feature_tools({}) == []
    assert [
        t.name for t in experimental_feature_tools({"conversation_reactions": True})
    ] == [
        "react",
        "unreact",
    ]


def _runtime() -> LaunchRuntime:
    return LaunchRuntime(acp_skill_sourcing="native", browser_available=False)


def test_finalize_adds_feature_tools_to_openhands_agents_only():
    tools = experimental_feature_tools({"conversation_reactions": True})
    agent = Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[])
    acp = ACPAgent(acp_command=["echo"])

    launched = finalize(agent, _runtime(), experimental_feature_tools=tools)
    launched_acp = finalize(acp, _runtime(), experimental_feature_tools=tools)

    assert [t.name for t in launched.agent.tools] == ["react", "unreact"]
    assert launched_acp.agent.tools == acp.tools


@pytest.mark.asyncio
async def test_new_conversations_get_reaction_tools_only_while_on(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def request() -> StartConversationRequest:
        return StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
        )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        off, _ = await service.start_conversation(request())

        def turn_on(settings: PersistedSettings) -> PersistedSettings:
            settings.experimental_features = {"conversation_reactions": True}
            return settings

        get_settings_store().update(turn_on)
        on, _ = await service.start_conversation(request())

        tool_names = {}
        for info in (off, on):
            event_service = await service.get_event_service(info.id)
            assert event_service is not None
            agent = event_service.get_conversation().agent
            tool_names[info.id] = [t.name for t in agent.tools]

    assert tool_names[off.id] == []
    assert tool_names[on.id] == ["react", "unreact"]
