"""Tests for active_meta_profile persistence and propagation (Option A)."""

from openhands.agent_server.persistence.models import PersistedSettings
from openhands.sdk.settings.model import OpenHandsAgentSettings


def test_update_sets_top_level_and_propagates_into_agent_settings() -> None:
    settings = PersistedSettings()

    settings.update({"active_meta_profile": "balanced"})

    assert settings.active_meta_profile == "balanced"
    # OpenHandsAgentSettings (default) gains the routing tool config.
    agent = settings.agent_settings
    assert isinstance(agent, OpenHandsAgentSettings)
    assert agent.active_meta_profile == "balanced"
    assert agent.enable_classify_and_switch_llm_tool is True


def test_update_clearing_disables_tool() -> None:
    settings = PersistedSettings()
    settings.update({"active_meta_profile": "balanced"})

    settings.update({"active_meta_profile": None})

    assert settings.active_meta_profile is None
    agent = settings.agent_settings
    assert isinstance(agent, OpenHandsAgentSettings)
    assert agent.active_meta_profile is None
    assert agent.enable_classify_and_switch_llm_tool is False


def test_update_without_active_meta_profile_leaves_it_unchanged() -> None:
    settings = PersistedSettings()
    settings.update({"active_meta_profile": "balanced"})

    # An unrelated update must not reset the meta-profile.
    settings.update({"active_profile": "fast"})

    assert settings.active_meta_profile == "balanced"
    agent = settings.agent_settings
    assert isinstance(agent, OpenHandsAgentSettings)
    assert agent.active_meta_profile == "balanced"


def test_active_meta_profile_round_trips_through_serialization() -> None:
    settings = PersistedSettings()
    settings.update({"active_meta_profile": "balanced"})

    dumped = settings.model_dump(mode="json")
    assert dumped["active_meta_profile"] == "balanced"

    reloaded = PersistedSettings.from_persisted(dumped)
    assert reloaded.active_meta_profile == "balanced"
    agent = reloaded.agent_settings
    assert isinstance(agent, OpenHandsAgentSettings)
    assert agent.active_meta_profile == "balanced"
