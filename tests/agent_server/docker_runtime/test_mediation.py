from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.mediation import (
    PreparedStart,
    container_launch_runtime,
    finish_start,
    prepare_start,
    serialize_start,
)
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_llm_profile_store,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.request import (
    AgentLaunchAdditions,
    StartConversationRequest,
)
from openhands.sdk.profiles import OpenHandsAgentProfile, UnresolvedProfileReferences
from openhands.sdk.secret import LookupSecret, StaticSecret
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


async def _finish(prepared: PreparedStart) -> StartConversationRequest:
    return await finish_start(
        prepared,
        container_launch_runtime(prepared.settings, browser_available=False),
    )


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

    prepared = await prepare_start(request.model_dump(mode="json"), runtime_config)
    assert prepared.launched is None
    assert looked_up == ["http://127.0.0.1:8123/api/settings/secrets/SELECTED"]
    assert isinstance(prepared.request.secrets["SELECTED"], StaticSecret)

    finished = await _finish(prepared)
    identity = RuntimeProvisioningStore(runtime_config).create(uuid4())
    payload = serialize_start(finished, identity)
    assert "selected-value" not in str(payload)
    assert "outer-session" not in str(payload)
    received = StartConversationRequest.model_validate(
        payload, context={"cipher": identity.cipher}
    )
    assert received.secrets["SELECTED"].get_value() == "selected-value"


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
    prepared = await prepare_start(request.model_dump(mode="json"), runtime_config)
    finished = await _finish(prepared)
    context = finished.agent.agent_context
    assert context is not None
    assert context.secrets is not None
    source = context.secrets["CONTEXT_SECRET"]
    assert isinstance(source, StaticSecret)
    assert source.get_value() == "context-value"


@pytest.mark.asyncio
async def test_profile_launch_scopes_secrets_and_stamps_provenance(
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
        tools=[],
        mcp_server_refs=[],
        secret_refs=["ALLOWED"],
    )
    get_agent_profile_store().save(profile)
    monkeypatch.setattr(
        "openhands.agent_server.agent_launch.discover_profile_skills",
        lambda: [],
    )
    monkeypatch.setattr(
        LookupSecret, "get_value", lambda secret: secret.url.rsplit("/", 1)[-1]
    )
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent_profile_id=profile.id,
        secrets={
            name: LookupSecret(url=f"/api/settings/secrets/{name}")
            for name in ("ALLOWED", "UNRELATED")
        },
    )

    prepared = await prepare_start(request.model_dump(mode="json"), runtime_config)
    assert set(prepared.request.secrets) == {"ALLOWED"}
    assert prepared.launched is not None
    assert prepared.launched.agent_profile_id == profile.id
    assert prepared.launched.revision == profile.revision

    finished = await _finish(prepared)
    assert finished.agent_profile_id is None
    assert set(finished.secrets) == {"ALLOWED"}
    api_key = finished.agent.llm.api_key
    assert isinstance(api_key, SecretStr)
    assert api_key.get_secret_value() == "model-key"


@pytest.mark.asyncio
async def test_dangling_llm_profile_ref_fails_before_any_container_work(
    tmp_path, monkeypatch
):
    runtime_config = config(tmp_path, monkeypatch)
    profile = OpenHandsAgentProfile(
        name="docker-dangling-profile",
        llm_profile_ref="never-saved",
        tools=[],
    )
    get_agent_profile_store().save(profile)
    monkeypatch.setattr(
        "openhands.agent_server.agent_launch.discover_profile_skills",
        lambda: [],
    )
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent_profile_id=profile.id,
    )

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        await prepare_start(request.model_dump(mode="json"), runtime_config)
    assert exc_info.value.llm_profile_ref == "never-saved"


@pytest.mark.asyncio
async def test_serialize_start_drops_launch_only_fields(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test"), tools=[]),
        agent_launch_additions=AgentLaunchAdditions(
            system_message_suffix_append="<RUNTIME_SERVICES/>"
        ),
    )
    identity = RuntimeProvisioningStore(runtime_config).create(uuid4())

    payload = serialize_start(request, identity)

    assert "agent_launch_additions" not in payload
    assert "agent_profile_id" not in payload
    assert "agent_profile" not in payload
    assert "agent_settings" not in payload
