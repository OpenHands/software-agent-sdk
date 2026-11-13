"""Tests for the user-facing experimental features (feature flags)."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server import experimental_features
from openhands.agent_server.api import add_domain_error_handler
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_settings_store
from openhands.agent_server.experimental_features import (
    RUNTIME_EXPERIMENTAL_FEATURES_ENV,
    ExperimentalFeatureDefinition,
    experimental_feature_tools,
    host_experimental_features,
)
from openhands.agent_server.experimental_features_router import (
    experimental_features_router,
)
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import PersistedSettings, ServerStores
from openhands.sdk import LLM, Agent
from openhands.sdk.agent import ACPAgent
from openhands.sdk.launch.finalize import LaunchRuntime, finalize
from openhands.sdk.workspace import LocalWorkspace


QA_FEATURE = ExperimentalFeatureDefinition(
    key="qa_feature", label="QA feature", description="A feature for tests."
)


@pytest.fixture
def qa_feature(monkeypatch) -> ExperimentalFeatureDefinition:
    monkeypatch.setattr(experimental_features, "EXPERIMENTAL_FEATURES", [QA_FEATURE])
    return QA_FEATURE


@pytest.fixture
async def client(server_stores: ServerStores) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    add_domain_error_handler(app)
    app.dependency_overrides[get_settings_store] = lambda: server_stores.settings
    app.include_router(experimental_features_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_listing_needs_no_settings_file_and_creates_none(
    client, qa_feature, server_stores
):
    response = await client.get("/api/settings/experimental")

    assert response.status_code == 200
    assert response.json() == [
        {
            "key": "qa_feature",
            "label": "QA feature",
            "description": "A feature for tests.",
            "enabled": False,
        }
    ]
    assert not server_stores.settings._path.exists()


@pytest.mark.asyncio
async def test_turning_a_feature_on_and_off_is_saved(client, qa_feature, server_stores):
    on = await client.patch(
        "/api/settings/experimental/qa_feature", json={"enabled": True}
    )
    listed_on = await client.get("/api/settings/experimental")
    off = await client.patch(
        "/api/settings/experimental/qa_feature", json={"enabled": False}
    )

    assert on.status_code == 200
    assert on.json()["enabled"] is True
    assert listed_on.json()[0]["enabled"] is True
    assert off.json()["enabled"] is False
    stored = server_stores.settings.load()
    assert stored is not None
    assert stored.experimental_features == {"qa_feature": False}


@pytest.mark.asyncio
async def test_unknown_features_are_refused(client, qa_feature, server_stores):
    response = await client.patch(
        "/api/settings/experimental/time_travel", json={"enabled": True}
    )

    assert response.status_code == 404
    assert not server_stores.settings._path.exists()


@pytest.mark.asyncio
async def test_switching_a_feature_keeps_the_other_settings(
    client, qa_feature, server_stores
):
    server_stores.settings.save(
        PersistedSettings(misc_settings={"kept": True}, active_profile="mine")
    )

    await client.patch("/api/settings/experimental/qa_feature", json={"enabled": True})

    stored = server_stores.settings.load()
    assert stored is not None
    assert stored.misc_settings == {"kept": True}
    assert stored.active_profile == "mine"
    assert stored.experimental_features == {"qa_feature": True}


@pytest.mark.asyncio
async def test_a_stored_flag_nobody_knows_is_not_listed_and_is_kept(
    client, qa_feature, server_stores
):
    """A flag saved by another version of the server does no harm."""
    server_stores.settings.save(
        PersistedSettings(experimental_features={"from_the_future": True})
    )

    listed = await client.get("/api/settings/experimental")
    await client.patch("/api/settings/experimental/qa_feature", json={"enabled": True})

    assert [f["key"] for f in listed.json()] == ["qa_feature"]
    stored = server_stores.settings.load()
    assert stored is not None
    assert stored.experimental_features == {
        "from_the_future": True,
        "qa_feature": True,
    }


@pytest.mark.asyncio
async def test_a_corrupted_settings_file_is_not_overwritten(
    client, qa_feature, server_stores
):
    path = server_stores.settings._path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    listed = await client.get("/api/settings/experimental")
    response = await client.patch(
        "/api/settings/experimental/qa_feature", json={"enabled": True}
    )

    assert listed.json()[0]["enabled"] is False
    assert response.status_code == 409
    assert path.read_text() == "{not json"


def test_settings_saved_before_experimental_features_load_with_none_on():
    settings = PersistedSettings()
    saved = settings.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    del saved["experimental_features"]

    loaded = PersistedSettings.from_persisted(json.loads(json.dumps(saved)))

    assert loaded.experimental_features == {}
    assert loaded.schema_version == settings.schema_version


@pytest.mark.asyncio
async def test_conversation_reactions_is_listed_and_off(client):
    response = await client.get("/api/settings/experimental")

    # Projects (#6602) is listed after it.
    assert [(f["key"], f["enabled"]) for f in response.json()][0] == (
        "conversation_reactions",
        False,
    )


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
async def test_new_conversations_get_reaction_tools_only_while_on(
    tmp_path, server_stores
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def request() -> StartConversationRequest:
        return StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
        )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations", stores=server_stores
    ) as service:
        off, _ = await service.start_conversation(request())

        def turn_on(settings: PersistedSettings) -> PersistedSettings:
            settings.experimental_features = {"conversation_reactions": True}
            return settings

        server_stores.settings.update(turn_on)
        on, _ = await service.start_conversation(request())

        tool_names = {}
        for info in (off, on):
            event_service = await service.get_event_service(info.id)
            assert event_service is not None
            agent = event_service.get_conversation().agent
            tool_names[info.id] = [t.name for t in agent.tools]

    assert tool_names[off.id] == []
    assert tool_names[on.id] == ["react", "unreact"]


def test_host_switches_are_read_only_inside_a_runtime(monkeypatch):
    monkeypatch.delenv(RUNTIME_EXPERIMENTAL_FEATURES_ENV, raising=False)
    assert host_experimental_features() is None

    monkeypatch.setenv(
        RUNTIME_EXPERIMENTAL_FEATURES_ENV,
        json.dumps({"conversation_reactions": True, "unknown": True}),
    )
    assert host_experimental_features() == {"conversation_reactions": True}

    for broken in ("", "not json", "[1]", '{"conversation_reactions": "yes"}'):
        monkeypatch.setenv(RUNTIME_EXPERIMENTAL_FEATURES_ENV, broken)
        assert host_experimental_features() == {}


@pytest.mark.asyncio
async def test_a_runtime_takes_its_tools_from_the_host_switches(tmp_path, monkeypatch):
    # The runtime container's own settings are empty (flag off); the host
    # passes the user's switches in the environment.
    monkeypatch.setenv(
        RUNTIME_EXPERIMENTAL_FEATURES_ENV, json.dumps({"conversation_reactions": True})
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        info, _ = await service.start_conversation(
            StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=str(workspace)),
            )
        )
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        agent = event_service.get_conversation().agent

    assert [t.name for t in agent.tools] == ["react", "unreact"]
