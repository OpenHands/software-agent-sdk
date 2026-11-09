import pytest
from fastapi import HTTPException

from openhands.agent_server.config import Config
from openhands.agent_server.launch import launch_source, server_launch_stores
from openhands.agent_server.openai.service import _create_conversation_request
from openhands.agent_server.persistence import (
    PersistedSettings,
    ServerStores,
)
from openhands.sdk import LLM
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.launch import ResolvedLaunch
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings


@pytest.fixture
def config(tmp_path, monkeypatch) -> Config:
    monkeypatch.setattr(
        "openhands.agent_server.launch.discover_profile_skills", lambda: []
    )
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
        session_api_keys=[],
    )
    stores = ServerStores.from_config(config)
    stores.llm_profiles.save("fast", LLM(model="fast-model"))
    stores.llm_profiles.save("slow", LLM(model="slow-model"))
    return config


def _activate(config: Config, profile: OpenHandsAgentProfile | ACPAgentProfile):
    stores = ServerStores.from_config(config)
    stores.agent_profiles.save(profile)
    stores.settings.save(PersistedSettings(active_agent_profile_id=str(profile.id)))


def _gateway_request(config: Config, model: str) -> StartConversationRequest:
    return _create_conversation_request(
        model=model,
        system_text="Answer briefly.",
        user_content=[],
        config=config,
        stores=ServerStores.from_config(config),
        conversation_id=None,
    )


def _launch(config: Config, request: StartConversationRequest) -> ResolvedLaunch:
    stores = ServerStores.from_config(config)
    settings = stores.settings.load() or PersistedSettings()
    source = launch_source(
        request,
        lambda: server_launch_stores(settings, stores, config.cipher),
        config.cipher,
    )
    assert isinstance(source, ResolvedLaunch)
    return source


def test_the_gateway_launches_an_openhands_profile_with_the_model_llm(config):
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="slow")
    _activate(config, profile)

    launched = _launch(config, _gateway_request(config, "openhands_fast"))

    assert isinstance(launched.settings, OpenHandsAgentSettings)
    assert launched.settings.llm.model == "fast-model"
    assert launched.profile is not None
    assert launched.profile.agent_profile_id == profile.id


def test_the_gateway_launches_an_acp_profile_with_its_own_model(config):
    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    _activate(config, profile)

    launched = _launch(config, _gateway_request(config, "openhands_fast"))

    assert isinstance(launched.settings, ACPAgentSettings)
    assert launched.profile is not None
    assert launched.profile.agent_profile_id == profile.id
    assert launched.profile.llm_profile_ref is None


def test_without_an_active_profile_the_gateway_activates_default(config):
    ServerStores.from_config(Config()).agent_profiles.save(
        OpenHandsAgentProfile(name="inactive", llm_profile_ref="slow")
    )
    ServerStores.from_config(config).settings.save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(system_message_suffix="Global suffix.")
            )
        )
    )

    launched = _launch(config, _gateway_request(config, "openhands_fast"))

    assert isinstance(launched.settings, OpenHandsAgentSettings)
    assert launched.settings.llm.model == "fast-model"
    assert launched.settings.agent_context.system_message_suffix == "Global suffix."
    assert launched.profile is not None
    assert launched.profile.llm_profile_ref == "fast"
    default = ServerStores.from_config(Config()).agent_profiles.load("default")
    assert launched.profile.agent_profile_id == default.id
    settings = ServerStores.from_config(config).settings.load()
    assert settings is not None
    assert settings.active_agent_profile_id == str(default.id)


@pytest.mark.parametrize(
    "profile",
    [
        OpenHandsAgentProfile(name="oh", llm_profile_ref="slow"),
        ACPAgentProfile(name="acp", acp_server="claude-code"),
    ],
    ids=["openhands", "acp"],
)
def test_an_unknown_model_is_not_found_whatever_the_profile(config, profile):
    _activate(config, profile)

    with pytest.raises(HTTPException) as exc_info:
        _gateway_request(config, "openhands_missing")

    assert exc_info.value.status_code == 404
