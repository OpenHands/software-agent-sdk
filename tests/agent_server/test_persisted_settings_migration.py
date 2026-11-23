from openhands.agent_server.persistence import PersistedSettings
from openhands.sdk.settings import OpenHandsAgentSettings


def test_unversioned_nested_agent_settings_migrate_as_a_legacy_row():
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 1,
            "agent_settings": {"llm": {"model": "m"}, "tools": []},
        }
    )

    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    assert settings.agent_settings.tools is None
