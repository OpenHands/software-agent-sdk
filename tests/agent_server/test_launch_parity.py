"""``materialize`` and a real launch agree, for every agent source."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.launch import (
    container_browser_enabled,
    forward_to_runtime,
    launch_source,
    server_launch_stores,
    target_launch_runtime,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
    reset_stores,
)
from openhands.sdk import LLM
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.launch import finalize, preview_launch, resolve
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.settings.model import (
    OpenHandsAgentSettings,
    validate_agent_settings,
)
from openhands.sdk.skills import Skill
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


CATALOG = [
    Skill(name="alpha", content="a", description="alpha skill"),
    Skill(name="beta", content="b", description="beta skill"),
]


@pytest.fixture
def server(tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    reset_stores()
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr("parity-key"),
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
    )
    (tmp_path / "workspace").mkdir()
    get_settings_store(config).save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                mcp_config={
                    "fetch": MCPServer(command="echo", args=["fetch"]),
                    "other": MCPServer(command="echo", args=["other"]),
                }
            )
        )
    )
    get_llm_profile_store().save(
        "default",
        LLM(model="parity-model", api_key=SecretStr("sk-parity")),
        include_secrets=True,
        cipher=config.cipher,
    )
    with (
        patch("openhands.agent_server.launch.discover_profile_skills", lambda: CATALOG),
        TestClient(create_app(config)) as client,
    ):
        yield client
    reset_stores()


def _save(profile: OpenHandsAgentProfile) -> OpenHandsAgentProfile:
    get_agent_profile_store().save(profile)
    return profile


def _profile(name: str) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name=name,
        llm_profile_ref="default",
        mcp_server_refs=["fetch"],
        disabled_skills=["beta"],
        system_message_suffix="PROFILE SUFFIX",
        tool_concurrency_limit=3,
    )


def _launch(client: TestClient, workspace: str, **source: Any) -> dict[str, Any]:
    response = client.post(
        "/api/conversations",
        params={"include_skills": True},
        json={"workspace": {"working_dir": workspace}, **source},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _tool_view(agent: dict[str, Any]) -> tuple[list[str], list[str]]:
    return [tool["name"] for tool in agent["tools"]], agent["include_default_tools"]


def _preview_tool_view(preview_settings: dict[str, Any]) -> tuple[list[str], list[str]]:
    agent = validate_agent_settings(preview_settings).create_agent()
    return _tool_view(agent.model_dump(mode="json"))


def _agent_view(agent: dict[str, Any]) -> dict[str, Any]:
    context = agent["agent_context"]
    return {
        "kind": agent["kind"],
        "llm": (agent["llm"]["model"], agent["llm"]["stream"]),
        "tools": [tool["name"] for tool in agent["tools"]],
        "include_default_tools": agent["include_default_tools"],
        "mcp": sorted(agent["mcp_config"]),
        "skills": [skill["name"] for skill in context["skills"]],
        "suffix": context["system_message_suffix"],
        "disabled_skills": context["disabled_skills"],
        "load_project_skills": context["load_project_skills"],
        "tool_concurrency_limit": agent["tool_concurrency_limit"],
        "condenser": (agent["condenser"] or {}).get("kind"),
    }


@pytest.mark.parametrize("browser", [True, False])
def test_materialize_matches_a_stored_profile_launch(server, tmp_path, browser):
    profile = _save(_profile("named"))
    with patch("openhands.agent_server.launch.is_tool_usable", return_value=browser):
        preview = server.post(f"/api/agent-profiles/{profile.name}/materialize").json()
        launched = _launch(
            server, str(tmp_path / "workspace"), agent_profile_id=str(profile.id)
        )

    agent = launched["agent"]
    assert preview["valid"] is True
    settings = preview["resolved_settings"]
    assert _preview_tool_view(settings) == _tool_view(agent)
    assert ("browser_tool_set" in _tool_view(agent)[0]) is browser
    assert preview["resolved_skills"] == [
        skill["name"] for skill in agent["agent_context"]["skills"]
    ]
    assert settings["llm"]["model"] == agent["llm"]["model"]
    assert sorted(settings["mcp_config"]) == sorted(agent["mcp_config"])
    assert (
        settings["agent_context"]["system_message_suffix"]
        == agent["agent_context"]["system_message_suffix"]
    )
    assert launched["launched_agent_profile"]["agent_profile_id"] == str(profile.id)


def test_default_and_a_copy_of_it_launch_the_same_agent(server, tmp_path):
    default = _save(_profile("default"))
    copy = _save(_profile("default-copy"))
    workspace = str(tmp_path / "workspace")

    with patch("openhands.agent_server.launch.is_tool_usable", return_value=True):
        from_default = _launch(server, workspace, agent_profile_id=str(default.id))
        from_copy = _launch(server, workspace, agent_profile_id=str(copy.id))

    assert _agent_view(from_default["agent"]) == _agent_view(from_copy["agent"])


def test_stored_and_agent_settings_sources_launch_the_same_agent(server, tmp_path):
    stored = _save(_profile("named"))
    workspace = str(tmp_path / "workspace")
    config = server.app.state.config
    settings = get_settings_store(config).load()
    assert settings is not None
    resolved = resolve(stored, server_launch_stores(settings, config.cipher))
    agent_settings = resolved.settings.model_dump(
        mode="json", context={"expose_secrets": True}
    )

    with patch("openhands.agent_server.launch.is_tool_usable", return_value=True):
        from_stored = _launch(server, workspace, agent_profile_id=str(stored.id))
        from_settings = _launch(server, workspace, agent_settings=agent_settings)

    assert _agent_view(from_settings["agent"]) == _agent_view(from_stored["agent"])
    assert from_settings["launched_agent_profile"] is None


@pytest.mark.parametrize("image_has_browser", [True, False])
def test_a_docker_host_preview_matches_what_the_container_launches(
    server, tmp_path, image_has_browser
):
    profile = _save(_profile("named"))
    host = server.app.state.config.model_copy(
        update={
            "conversation_runtime": "docker",
            "conversation_image_has_browser": image_has_browser,
        }
    )
    settings = get_settings_store(host).load()
    assert settings is not None
    stores = server_launch_stores(settings, host.cipher)

    preview = preview_launch(profile, stores, target_launch_runtime(host))

    container_cipher = Cipher("container-key")
    request = StartConversationRequest(
        agent_profile_id=profile.id, workspace=LocalWorkspace(working_dir="/workspace")
    )
    payload = forward_to_runtime(
        request,
        launch_source(request, lambda: stores, host.cipher),
        {},
        container_cipher,
        load_memory=False,
    )
    received = StartConversationRequest.model_validate(payload)
    container = host.model_copy(
        update={
            "conversation_runtime": "local",
            "acp_skill_sourcing": "openhands_managed",
            "enable_browser": container_browser_enabled(host),
        }
    )
    with patch("openhands.agent_server.launch.is_tool_usable", return_value=True):
        agent = finalize(
            launch_source(received, lambda: stores, container_cipher),
            target_launch_runtime(container),
        ).agent

    assert preview.resolved_settings is not None
    launched_tools = _tool_view(agent.model_dump(mode="json"))
    assert _preview_tool_view(preview.resolved_settings) == launched_tools
    assert ("browser_tool_set" in launched_tools[0]) is image_has_browser
    assert agent.agent_context is not None
    assert preview.resolved_skills == [s.name for s in agent.agent_context.skills]
