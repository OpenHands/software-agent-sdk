from openhands.agent_server.persistence import (
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    PersistedSettings,
)
from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    OpenHandsAgentSettings,
)


def test_unversioned_nested_agent_settings_migrate_as_a_legacy_row():
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 1,
            "agent_settings": {"llm": {"model": "m"}, "tools": []},
        }
    )

    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    assert settings.agent_settings.tools is None


def test_v5_settings_file_stores_a_no_op_condenser_as_the_disabled_summarizer():
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 5,
            "agent_settings": {
                "schema_version": 8,
                "llm": {"model": "m"},
                "condenser": {"enabled": True, "condenser_kind": "no_op"},
            },
        }
    )

    assert settings.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert settings.agent_settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    condenser = settings.agent_settings.condenser
    assert isinstance(condenser, LLMSummarizingCondenserSettings)
    assert condenser.enabled is False


def test_v6_settings_file_keeps_a_no_op_condenser():
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": PERSISTED_SETTINGS_SCHEMA_VERSION,
            "agent_settings": {
                "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
                "llm": {"model": "m"},
                "condenser": {"enabled": True, "condenser_kind": "no_op"},
            },
        }
    )

    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    assert isinstance(settings.agent_settings.condenser, NoOpCondenserSettings)
