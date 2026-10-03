import pytest
from fastapi import HTTPException

from openhands.agent_server.config import Config
from openhands.agent_server.launch import launch_source, server_launch_stores
from openhands.agent_server.openai.service import _create_conversation_request
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk import LLM
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.launch import ResolvedLaunch
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings


@pytest.fixture
def config(tmp_path, monkeypatch) -> Config:
    monkeypatch.setattr(
        "openhands.agent_server.launch.discover_profile_skills", lambda: []
    )
    get_llm_profile_store().save("fast", LLM(model="fast-model"))
    get_llm_profile_store().save("slow", LLM(model="slow-model"))
    return Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
        session_api_keys=[],
    )


def _activate(config: Config, profile: OpenHandsAgentProfile | ACPAgentProfile):
    get_agent_profile_store().save(profile)
    get_settings_store(config).save(
        PersistedSettings(active_agent_profile_id=str(profile.id))
    )


def _gateway_request(config: Config, model: str) -> StartConversationRequest:
    return _create_conversation_request(
        model=model,
        system_text="Answer briefly.",
        user_content=[],
        config=config,
        conversation_id=None,
    )


def _launch(config: Config, request: StartConversationRequest) -> ResolvedLaunch:
    settings = get_settings_store(config).load() or PersistedSettings()
    source = launch_source(
        request, lambda: server_launch_stores(settings, config.cipher), config.cipher
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
