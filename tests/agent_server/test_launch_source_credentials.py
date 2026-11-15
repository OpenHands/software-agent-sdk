"""A caller-supplied agent source resolves the provider connections it names.

A client that builds its own ``agent`` payload (the local planner conversation
does exactly this) carries ``provider_connection_id`` instead of an API key, so
without resolution here the run starts keyless and fails authentication.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from openhands.agent_server.launch import launch_source, server_launch_stores
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_llm_profile_store,
    get_provider_connections_store,
    get_settings_store,
    reset_stores,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.launch import AgentLaunchError, ResolvedLaunch
from openhands.sdk.llm.provider_connection_store import ProviderConnection
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.workspace import LocalWorkspace


CONNECTION_ID = "shared-provider"
CONNECTION_KEY = "sk-shared"


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A server whose stores hold one provider connection and one linked profile."""
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    reset_stores()
    from openhands.agent_server.config import Config

    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr("launch-credential-key"),
    )
    now = 1
    get_provider_connections_store().create(
        ProviderConnection(
            id=CONNECTION_ID,
            display_name="Shared provider",
            api_key=SecretStr(CONNECTION_KEY),
            base_url="https://provider.example/v1",
            created_at=now,
            updated_at=now,
        ),
        cipher=config.cipher,
    )
    get_llm_profile_store().save(
        "linked",
        LLM(model="gpt-4o", provider_connection_id=CONNECTION_ID),
        cipher=config.cipher,
    )
    get_settings_store(config).save(
        PersistedSettings(agent_settings=OpenHandsAgentSettings())
    )
    yield config
    reset_stores()


def _stores(config):
    settings = get_settings_store(config).load()
    assert settings is not None
    return lambda: server_launch_stores(settings, config.cipher)


def _linked_llm(**kwargs: Any) -> LLM:
    """What a client sends for a profile linked to a provider connection."""
    return LLM(
        model="gpt-4o",
        provider_connection_id=CONNECTION_ID,
        stream=True,
        **kwargs,
    )


def _key(llm: LLM) -> str | None:
    return llm.api_key.get_secret_value() if llm.api_key else None


def test_agent_source_resolves_the_connection_it_references(server):
    request = StartConversationRequest(
        agent=Agent(llm=_linked_llm(), tools=[]),
        workspace=LocalWorkspace(working_dir="/workspace"),
    )

    source = launch_source(request, _stores(server), server.cipher)

    assert isinstance(source, Agent)
    assert _key(source.llm) == CONNECTION_KEY
    assert source.llm.base_url == "https://provider.example/v1"
    # The reference survives, so a later rotation still resolves.
    assert source.llm.provider_connection_id == CONNECTION_ID


def test_agent_settings_source_resolves_the_connection_it_references(server):
    request = StartConversationRequest(
        agent_settings={
            "kind": "openhands",
            "llm": {
                "model": "gpt-4o",
                "provider_connection_id": CONNECTION_ID,
                "stream": True,
            },
            "tools": [],
        },
        workspace=LocalWorkspace(working_dir="/workspace"),
    )

    source = launch_source(request, _stores(server), server.cipher)

    assert isinstance(source, ResolvedLaunch)
    assert _key(source.settings.llm) == CONNECTION_KEY


def test_nested_llms_resolve_too(server):
    """The condenser's LLM is a separate config and carries the same reference."""
    request = StartConversationRequest(
        agent=Agent(
            llm=_linked_llm(),
            tools=[],
            condenser=LLMSummarizingCondenser(
                llm=_linked_llm(usage_id="condenser"), max_size=20
            ),
        ),
        workspace=LocalWorkspace(working_dir="/workspace"),
    )

    source = launch_source(request, _stores(server), server.cipher)

    assert isinstance(source, Agent)
    assert _key(source.condenser.llm) == CONNECTION_KEY
    assert source.condenser.llm.base_url == "https://provider.example/v1"


def test_a_source_without_a_reference_is_returned_unchanged(server):
    """The common path must not rebuild an agent that names no connection."""
    agent = Agent(llm=LLM(model="gpt-4o", api_key=SecretStr("sk-inline")), tools=[])
    request = StartConversationRequest(
        agent=agent, workspace=LocalWorkspace(working_dir="/workspace")
    )

    source = launch_source(request, _stores(server), server.cipher)

    assert source is agent


def test_a_dangling_reference_fails_the_launch(server):
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", provider_connection_id="ghost"), tools=[]),
        workspace=LocalWorkspace(working_dir="/workspace"),
    )

    with pytest.raises(AgentLaunchError, match="ghost"):
        launch_source(request, _stores(server), server.cipher)


def test_an_inline_key_still_wins_over_a_deleted_connection(server):
    """Only a keyless LLM depends on the connection; a legacy one keeps its key."""
    request = StartConversationRequest(
        agent=Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("sk-legacy"),
                provider_connection_id="ghost",
            ),
            tools=[],
        ),
        workspace=LocalWorkspace(working_dir="/workspace"),
    )

    source = launch_source(request, _stores(server), server.cipher)

    assert isinstance(source, Agent)
    assert _key(source.llm) == "sk-legacy"


def test_a_conversation_started_with_a_linked_agent_runs_with_real_credentials(
    server, tmp_path
):
    """The regression: POST /api/conversations with a keyless linked agent."""
    import tempfile

    from fastapi.testclient import TestClient

    from openhands.agent_server.api import create_app

    workspace = tempfile.mkdtemp(dir=tmp_path)
    with TestClient(create_app(server)) as client:
        response = client.post(
            "/api/conversations",
            json={
                "agent": {
                    "kind": "Agent",
                    "llm": {
                        "model": "gpt-4o",
                        "provider_connection_id": CONNECTION_ID,
                        "stream": True,
                    },
                    "tools": [],
                },
                "workspace": {"working_dir": workspace},
            },
        )

    assert response.status_code == 201, response.text
    llm = response.json()["agent"]["llm"]
    assert llm["provider_connection_id"] == CONNECTION_ID
    # Only the connection knows this base_url, so the launch resolved it; the
    # key itself is resolved too and never leaves the server unredacted.
    assert llm["base_url"] == "https://provider.example/v1"
    assert llm["api_key"] is None
