from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.provisioning import (
    RuntimeIdentity,
    RuntimeProvisioningStore,
)
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.docker_runtime.routers import _prepare_forward
from openhands.agent_server.launch import launch_source
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.request import (
    AgentLaunchAdditions,
    StartConversationRequest,
)
from openhands.sdk.launch import LaunchRuntime, LaunchStores, ResolvedLaunch, finalize
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.secret import LookupSecret, StaticSecret
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.workspace import LocalWorkspace


def config(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8123")
    return Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer-key"),
        session_api_keys=["outer-session"],
    )


async def _forward(
    request: StartConversationRequest, runtime_config: Config, *, existing=False
) -> tuple[dict[str, Any], Any, RuntimeIdentity]:
    registry = cast(DockerConversationRegistry, SimpleNamespace(config=runtime_config))
    body = request.model_dump(mode="json", context={"expose_secrets": True})
    payload, launched = await _prepare_forward(body, registry, existing=existing)
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    return payload(identity.cipher), launched, identity


def _no_stores() -> LaunchStores:
    raise AssertionError("the container reads no store")


@pytest.mark.asyncio
async def test_materializes_request_secret_sources(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    looked_up = []

    def get_value(secret):
        looked_up.append(secret.url)
        return "selected-value"

    monkeypatch.setattr(LookupSecret, "get_value", get_value)
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test", api_key=SecretStr("model-key"))),
        secrets={
            "SELECTED": LookupSecret(
                url="/api/settings/secrets/SELECTED",
                headers={"X-Session-API-Key": "outer-session"},
            )
        },
    )

    payload, launched, identity = await _forward(request, runtime_config)

    assert launched is None
    assert looked_up == ["http://127.0.0.1:8123/api/settings/secrets/SELECTED"]
    assert "selected-value" not in str(payload)
    assert "outer-session" not in str(payload)
    assert "model-key" not in str(payload)
    received = StartConversationRequest.model_validate(
        payload, context={"cipher": identity.cipher}
    )
    assert received.secrets["SELECTED"].get_value() == "selected-value"
    agent = launch_source(received, _no_stores, identity.cipher)
    assert isinstance(agent, Agent)
    assert agent.llm.api_key is not None
    assert isinstance(agent.llm.api_key, SecretStr)
    assert agent.llm.api_key.get_secret_value() == "model-key"


@pytest.mark.asyncio
async def test_materializes_agent_context_secret_sources(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    monkeypatch.setattr(LookupSecret, "get_value", lambda secret: "context-value")
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(
            llm=LLM(model="test"),
            agent_context=AgentContext(
                secrets={"CONTEXT_SECRET": LookupSecret(url="/secret")}
            ),
        ),
    )

    payload, _, identity = await _forward(request, runtime_config)

    received = StartConversationRequest.model_validate(payload)
    agent = launch_source(received, _no_stores, identity.cipher)
    assert isinstance(agent, Agent)
    context = agent.agent_context
    assert context is not None and context.secrets is not None
    source = context.secrets["CONTEXT_SECRET"]
    assert isinstance(source, StaticSecret)
    assert source.get_value() == "context-value"


@pytest.mark.asyncio
async def test_the_host_resolves_a_profile_and_the_container_finalizes_it(
    tmp_path, monkeypatch
):
    runtime_config = config(tmp_path, monkeypatch)
    get_llm_profile_store().save(
        "docker-test-model",
        LLM(model="test", api_key=SecretStr("model-key")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    profile = OpenHandsAgentProfile(
        name="docker-test-profile",
        llm_profile_ref="docker-test-model",
        mcp_server_refs=[],
        secret_refs=["ALLOWED"],
    )
    get_agent_profile_store().save(profile)
    monkeypatch.setattr(
        "openhands.agent_server.launch.discover_profile_skills", lambda: []
    )
    monkeypatch.setattr(
        LookupSecret, "get_value", lambda secret: secret.url.rsplit("/", 1)[-1]
    )
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent_profile_id=profile.id,
        agent_launch_additions=AgentLaunchAdditions(
            system_message_suffix_append="RUNTIME"
        ),
        secrets={
            name: LookupSecret(url=f"/api/settings/secrets/{name}")
            for name in ("ALLOWED", "UNRELATED")
        },
    )

    payload, launched, identity = await _forward(request, runtime_config)

    assert launched is not None
    assert launched.agent_profile_id == profile.id
    assert "agent_profile_id" not in payload
    # The host leaves the default tool set, and so the browser, to the container.
    assert payload["agent_settings"]["tools"] is None
    received = StartConversationRequest.model_validate(
        payload, context={"cipher": identity.cipher}
    )
    assert set(received.secrets) == {"ALLOWED"}
    source = launch_source(received, _no_stores, identity.cipher)
    assert isinstance(source, ResolvedLaunch)
    agent = finalize(
        source,
        LaunchRuntime(browser_available=True, acp_skill_sourcing="openhands_managed"),
        additions=received.agent_launch_additions,
    ).agent
    assert isinstance(agent, Agent)
    assert agent.llm.model == "test"
    assert agent.llm.stream is True
    assert "browser_tool_set" in {tool.name for tool in agent.tools}
    assert agent.agent_context is not None
    assert agent.agent_context.system_message_suffix == "RUNTIME"


@pytest.mark.asyncio
async def test_the_host_forwards_its_memory_preference(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    get_settings_store(runtime_config).save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )
    )
    get_llm_profile_store().save("fast", LLM(model="fast-model"))
    profile = OpenHandsAgentProfile(name="p", llm_profile_ref="fast")
    get_agent_profile_store().save(profile)
    monkeypatch.setattr(
        "openhands.agent_server.launch.discover_profile_skills", lambda: []
    )
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent_profile_id=profile.id,
    )

    payload, _, identity = await _forward(request, runtime_config)

    assert payload["agent_settings"]["agent_context"]["load_memory"] is True
    received = StartConversationRequest.model_validate(payload)
    source = launch_source(received, _no_stores, identity.cipher)
    agent = finalize(
        source, LaunchRuntime(), additions=received.agent_launch_additions
    ).agent
    assert isinstance(agent, Agent)
    assert agent.llm.model == "fast-model"
    assert agent.agent_context is not None
    assert agent.agent_context.load_memory is True


@pytest.mark.asyncio
async def test_an_existing_conversation_is_not_resolved_again(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent_profile_id=uuid4(),
    )

    payload, launched, _ = await _forward(request, runtime_config, existing=True)

    assert launched is None
    assert payload["agent_profile_id"] == str(request.agent_profile_id)
    assert "agent_settings" not in payload
