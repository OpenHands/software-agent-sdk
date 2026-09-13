from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.mediation import (
    materialize_mcp_probe,
    materialize_start,
    materialize_title_profile,
    serialize_for_runtime,
)
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.persistence import get_llm_profile_store, get_secrets_store
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.secret import LookupSecret, SecretSource
from openhands.sdk.workspace import LocalWorkspace


@pytest.mark.asyncio
async def test_selected_secret_and_encrypted_agent_cross_runtime_boundary(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8123")
    config = Config(
        secret_key=SecretStr("outer-encryption"),
        session_api_keys=["outer-session"],
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
    )
    secrets = get_secrets_store(config)
    secrets.set_secret("ALLOWED", "allowed-canary")
    secrets.set_secret("UNRELATED", "unrelated-canary")
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("selected-model-key"))
        ),
        secrets={
            "ALLOWED": LookupSecret(
                url="/api/settings/secrets/ALLOWED",
                headers={"X-Session-API-Key": "outer-session"},
            )
        },
    )
    body = request.model_dump(mode="json", context={"cipher": config.cipher})
    body["secrets_encrypted"] = True
    resolved, launched = await materialize_start(body, config)
    assert launched is None
    identity = RuntimeProvisioningStore(config).create(uuid4())
    wire = serialize_for_runtime(resolved, identity)
    assert "outer-session" not in str(wire)
    assert "unrelated-canary" not in str(wire)
    assert "allowed-canary" not in str(wire)
    received = StartConversationRequest.model_validate(
        wire, context={"cipher": identity.cipher}
    )
    assert isinstance(received.agent.llm.api_key, SecretStr)
    assert received.agent.llm.api_key.get_secret_value() == "selected-model-key"
    assert received.secrets["ALLOWED"].get_value() == "allowed-canary"
    assert set(received.secrets) == {"ALLOWED"}


@pytest.mark.asyncio
async def test_external_lookup_is_not_reinterpreted_as_local_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(secret_key=SecretStr("outer-encryption"))
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test-model")),
        secrets={
            "secret": LookupSecret(
                url="https://external.invalid/api/settings/secrets/ALLOWED"
            )
        },
    )
    with pytest.raises(ValueError, match="External"):
        await materialize_start(request.model_dump(mode="json"), config)


@pytest.mark.asyncio
async def test_only_selected_title_profile_is_materialized(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(
        secret_key=SecretStr("outer-encryption"),
        conversations_path=tmp_path / "conversations",
    )
    profiles = get_llm_profile_store()
    profiles.save(
        "title",
        LLM(model="test-model", api_key=SecretStr("title-key")),
        include_secrets=True,
        cipher=config.cipher,
    )
    profiles.save(
        "unrelated",
        LLM(model="test-model", api_key=SecretStr("other-key")),
        include_secrets=True,
        cipher=config.cipher,
    )
    store = RuntimeProvisioningStore(config)
    identity = store.create(uuid4())
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test-model")),
        title_llm_profile="title",
    )
    await materialize_title_profile(request, store, identity)
    directory = store.runtime_dir(identity.conversation_id) / "persistence" / "profiles"
    assert sorted(path.name for path in directory.glob("*.json")) == ["title.json"]
    assert "title-key" not in (directory / "title.json").read_text()
    restored = LLMProfileStore(directory).load("title", cipher=identity.cipher)
    assert isinstance(restored.api_key, SecretStr)
    assert restored.api_key.get_secret_value() == "title-key"


@pytest.mark.asyncio
async def test_runtime_probe_rejects_outer_control_headers():
    config = Config(
        session_api_keys=["outer-session"], secret_key=SecretStr("outer-encryption")
    )
    with pytest.raises(ValueError, match="Outer control"):
        await materialize_mcp_probe(
            {
                "server": {
                    "url": "https://mcp.example/mcp",
                    "headers": {"Authorization": "Bearer outer-session"},
                }
            },
            config,
        )


@pytest.mark.asyncio
async def test_switch_retains_auxiliary_subscription(tmp_path, monkeypatch):
    from openhands.agent_server.docker_runtime.mediation import (
        has_auxiliary_subscription,
        mediate_mutation,
    )
    from openhands.sdk.context.condenser import LLMSummarizingCondenser

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(secret_key=SecretStr("outer-encryption"))
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(
            llm=LLM(model="test"),
            condenser=LLMSummarizingCondenser(
                llm=LLM(model="test", auth_type="subscription")
            ),
        ),
    )
    assert has_auxiliary_subscription(request)
    identity = (
        RuntimeProvisioningStore(config)
        .create(uuid4())
        .model_copy(update={"auxiliary_subscription": True})
    )
    _, _, grants = await mediate_mutation(
        "switch_llm",
        {"llm": {"model": "test", "api_key": "selected-key"}},
        config,
        identity,
    )
    assert grants is not None and grants.subscription == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [None, [], ["ALLOWED"]])
async def test_profile_secret_scope_precedes_docker_lookup(
    tmp_path, monkeypatch, allowed
):
    from openhands.agent_server.docker_runtime import mediation
    from openhands.agent_server.persistence import get_agent_profile_store
    from openhands.sdk.profiles import OpenHandsAgentProfile

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(secret_key=SecretStr("outer-encryption"))
    secrets = get_secrets_store(config)
    secrets.set_secret("ALLOWED", "allowed-canary")
    secrets.set_secret("UNRELATED", "unrelated-canary")
    get_llm_profile_store().save(
        "selected-model",
        LLM(model="test-model"),
        include_secrets=True,
        cipher=config.cipher,
    )
    profile = OpenHandsAgentProfile(
        name="scoped",
        llm_profile_ref="selected-model",
        tools=[],
        mcp_server_refs=[],
        secret_refs=allowed,
    )
    get_agent_profile_store().save(profile)
    monkeypatch.setattr(
        "openhands.agent_server.conversation_service.discover_profile_skills",
        lambda: [],
    )
    supplied: dict[str, SecretSource] = {
        "UNRELATED": LookupSecret(url="/api/settings/secrets/UNRELATED")
    }
    looked_up = []
    original = secrets.get_secret

    def get_secret(name):
        looked_up.append(name)
        return original(name)

    monkeypatch.setattr(secrets, "get_secret", get_secret)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir="/workspace"),
        secrets=supplied,
    )
    resolved, launched = await mediation.materialize_start(
        request.model_dump(mode="json", exclude_none=True), config
    )
    expected = {"UNRELATED"} if allowed is None else set(allowed)
    assert set(resolved.secrets) == expected
    assert set(looked_up) == expected
    assert launched is not None
    assert launched.agent_profile_id == profile.id
    assert resolved.agent_profile_id is None
    identity = RuntimeProvisioningStore(config).create(uuid4())
    wire = serialize_for_runtime(resolved, identity)
    assert "allowed-canary" not in str(wire)
    assert "unrelated-canary" not in str(wire)
    received = StartConversationRequest.model_validate(
        wire, context={"cipher": identity.cipher}
    )
    from openhands.sdk.conversation.secret_registry import SecretRegistry

    registry = SecretRegistry()
    registry.update_secrets(received.secrets)
    assert {info["name"] for info in registry.get_secret_infos()} == expected
    env = registry.get_secrets_as_env_vars("use ALLOWED and UNRELATED")
    assert set(env) == expected
    if allowed is not None:
        assert "unrelated-canary" not in str(env)

    from openhands.sdk.context.prompts.section import PromptContext
    from openhands.sdk.context.prompts.sections.dynamic import CustomSecretsSection

    context = PromptContext(secret_infos=tuple((name, None) for name in expected))
    section = CustomSecretsSection()
    prompt = section.render(context) if section.guard(context) else ""
    assert prompt is not None
    assert "allowed-canary" not in prompt
    assert "unrelated-canary" not in prompt
    assert ("$ALLOWED" in prompt) == ("ALLOWED" in expected)
    assert ("$UNRELATED" in prompt) == ("UNRELATED" in expected)
