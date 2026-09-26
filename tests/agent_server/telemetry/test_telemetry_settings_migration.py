"""Telemetry consent stays in ``misc_settings``.

Consent lives in ``misc_settings.telemetry.consent``, which needs no typed
field and no dedicated schema change because ``misc_settings`` already exists
and is already persisted. (The persisted-settings schema version has since
advanced for unrelated reasons — see ``PERSISTED_SETTINGS_SCHEMA_VERSION`` — but
never because of consent.)
"""

from datetime import datetime

import pytest

from openhands.agent_server.persistence.models import (
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    PersistedSettings,
)
from openhands.agent_server.telemetry.policy import resolve


def test_schema_version_tracks_nested_agent_settings_change():
    assert PERSISTED_SETTINGS_SCHEMA_VERSION == 4


def test_there_is_no_typed_consent_field():
    assert "telemetry_consent" not in PersistedSettings.model_fields
    assert "telemetry_consent_updated_at" not in PersistedSettings.model_fields


@pytest.mark.parametrize("version", [1, 2, 3])
def test_older_settings_still_load(version: int):
    settings = PersistedSettings.from_persisted(
        {"schema_version": version, "active_profile": "default"}
    )
    assert settings.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert settings.active_profile == "default"
    # No consent recorded anywhere means no consent.
    assert resolve(settings.misc_settings, env={}).enabled is False


def test_v2_settings_migrate_nested_runtime_datetime():
    before = datetime.now().astimezone()
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 2,
            "agent_settings": {
                "schema_version": 5,
                "agent_kind": "openhands",
                "llm": {"model": "test-model"},
                "agent_context": {"current_datetime": "2024-03-15T14:30:00Z"},
            },
        }
    )
    after = datetime.now().astimezone()

    assert settings.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert settings.agent_settings.schema_version == 7
    assert settings.agent_settings.agent_context is not None
    current_datetime = settings.agent_settings.agent_context.current_datetime
    assert isinstance(current_datetime, datetime)
    assert before <= current_datetime <= after
    payload = settings.model_dump(mode="json")
    assert "current_datetime" not in payload["agent_settings"]["agent_context"]


def test_v3_settings_migrate_nested_runtime_datetime():
    before = datetime.now().astimezone()
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 3,
            "agent_settings": {
                "schema_version": 6,
                "agent_kind": "openhands",
                "llm": {"model": "test-model"},
                "agent_context": {"current_datetime": "2024-03-15T14:30:00Z"},
            },
        }
    )
    after = datetime.now().astimezone()

    assert settings.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert settings.agent_settings.schema_version == 7
    assert settings.agent_settings.agent_context is not None
    current_datetime = settings.agent_settings.agent_context.current_datetime
    assert isinstance(current_datetime, datetime)
    assert before <= current_datetime <= after
    payload = settings.model_dump(mode="json")
    assert "current_datetime" not in payload["agent_settings"]["agent_context"]


def test_v3_settings_preserve_nested_explicit_no_datetime():
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 3,
            "agent_settings": {
                "schema_version": 6,
                "agent_kind": "acp",
                "acp_server": "codex",
                "agent_context": {"current_datetime": None},
            },
        }
    )

    assert settings.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert settings.agent_settings.schema_version == 7
    assert settings.agent_settings.agent_context is not None
    assert settings.agent_settings.agent_context.current_datetime is None
    payload = settings.model_dump(mode="json")
    assert payload["agent_settings"]["agent_context"]["current_datetime"] is None


def test_consent_round_trips_through_misc_settings():
    settings = PersistedSettings()
    settings.update({"misc_settings_diff": {"telemetry": {"consent": "granted"}}})
    assert settings.misc_settings["telemetry"]["consent"] == "granted"

    reloaded = PersistedSettings.from_persisted(settings.model_dump(mode="json"))
    assert resolve(reloaded.misc_settings, env={}).enabled is True


def test_revoking_through_misc_settings_disables():
    settings = PersistedSettings()
    settings.update({"misc_settings_diff": {"telemetry": {"consent": "granted"}}})
    settings.update({"misc_settings_diff": {"telemetry": {"consent": "denied"}}})
    assert resolve(settings.misc_settings, env={}).enabled is False


def test_a_newer_schema_version_is_still_rejected():
    with pytest.raises(ValueError, match="newer than supported"):
        PersistedSettings.from_persisted(
            {"schema_version": PERSISTED_SETTINGS_SCHEMA_VERSION + 1}
        )
