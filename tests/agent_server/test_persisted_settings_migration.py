from openhands.agent_server.persistence import PersistedSettings


def test_unversioned_nested_agent_settings_migrate_as_a_legacy_row():
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 1,
            "agent_settings": {"llm": {"model": "m"}, "tools": []},
        }
    )

    assert settings.agent_settings.tools is None
