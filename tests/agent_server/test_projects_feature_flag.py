"""Projects behind the ``projects`` experimental feature (off by default)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import project_wake
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.experimental_features import (
    PROJECT_TOOL_NAMES,
    experimental_feature_withheld_tools,
    feature_enabled_now,
    find_experimental_feature,
    is_enabled,
)
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk import LLM, Agent
from openhands.sdk.launch.finalize import LaunchRuntime, finalize
from openhands.sdk.tool.spec import Tool


pytestmark = pytest.mark.projects_off


def test_projects_is_an_experimental_feature_off_by_default():
    assert find_experimental_feature("projects") is not None
    assert not is_enabled({}, "projects")
    assert experimental_feature_withheld_tools({}) == list(PROJECT_TOOL_NAMES)
    assert experimental_feature_withheld_tools({"projects": True}) == []


def _app(tmp_path) -> TestClient:
    config = Config(
        static_files_path=None,
        conversations_path=tmp_path / "conversations",
    )
    return TestClient(create_app(config))


def test_projects_api_answers_404_while_off(tmp_path):
    with _app(tmp_path) as client:
        assert client.get("/api/projects").status_code == 404
        assert client.post("/api/projects", json={"name": "p"}).status_code == 404


def test_projects_api_follows_the_switch_at_once(tmp_path):
    with _app(tmp_path) as client:
        assert client.get("/api/projects").status_code == 404
        response = client.patch(
            "/api/settings/experimental/projects", json={"enabled": True}
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/projects").status_code == 200


@pytest.mark.asyncio
async def test_feature_enabled_now_reads_the_stored_switch(tmp_path, monkeypatch):
    monkeypatch.delenv("OH_RUNTIME_EXPERIMENTAL_FEATURES", raising=False)
    store = get_settings_store(Config(conversations_path=tmp_path / "c"))
    assert await feature_enabled_now("projects") is False
    settings = store.load() or PersistedSettings()
    store.save(
        settings.model_copy(update={"experimental_features": {"projects": True}})
    )
    assert await feature_enabled_now("projects") is True


@pytest.mark.asyncio
async def test_feature_enabled_now_uses_host_switches_in_a_runtime(monkeypatch):
    monkeypatch.setenv("OH_RUNTIME_EXPERIMENTAL_FEATURES", '{"projects": true}')
    assert await feature_enabled_now("projects") is True
    monkeypatch.setenv("OH_RUNTIME_EXPERIMENTAL_FEATURES", "{}")
    assert await feature_enabled_now("projects") is False


@pytest.mark.asyncio
async def test_no_wake_ups_while_off(monkeypatch):
    load = AsyncMock()
    monkeypatch.setattr(project_wake, "get_projects_store", load)
    service = SimpleNamespace(get_event_service=AsyncMock())
    assert await project_wake._coordinator(service, uuid4(), uuid4().hex) is None  # type: ignore[arg-type]
    load.assert_not_called()


def test_finalize_withholds_tools():
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test"),
        tools=[Tool(name="terminal"), Tool(name="list_project_conversations")],
    )
    launched = finalize(
        agent,
        LaunchRuntime(),
        withheld_tools=PROJECT_TOOL_NAMES,
    )
    assert [t.name for t in launched.agent.tools] == ["terminal"]
