from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.mediation import (
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
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.profiles import OpenHandsAgentProfile
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

    prepared, launched = await prepare_start(
        request.model_dump(mode="json"), runtime_config
    )
    assert launched is None
    assert looked_up == ["http://127.0.0.1:8123/api/settings/secrets/SELECTED"]
    assert isinstance(prepared.secrets["SELECTED"], StaticSecret)

    identity = RuntimeProvisioningStore(runtime_config).create(uuid4())
    payload = serialize_start(prepared, identity)
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
    prepared, _ = await prepare_start(request.model_dump(mode="json"), runtime_config)
    context = prepared.agent.agent_context
    assert context is not None
    assert context.secrets is not None
    source = context.secrets["CONTEXT_SECRET"]
    assert isinstance(source, StaticSecret)
    assert source.get_value() == "context-value"


@pytest.mark.asyncio
async def test_profile_uses_existing_resolver_and_secret_allowlist(
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
        "openhands.agent_server.conversation_service.discover_profile_skills",
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

    prepared, launched = await prepare_start(
        request.model_dump(mode="json"), runtime_config
    )
    assert set(prepared.secrets) == {"ALLOWED"}
    assert prepared.agent_profile_id is None
    assert launched is not None
    assert launched.agent_profile_id == profile.id
