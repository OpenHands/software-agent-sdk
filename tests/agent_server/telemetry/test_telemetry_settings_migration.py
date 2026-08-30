"""Persisted settings stay at schema v2.

Consent lives in ``misc_settings.telemetry.consent``, which needs no schema
change because ``misc_settings`` already exists and is already persisted.
"""

import pytest

from openhands.agent_server.persistence.models import (
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    PersistedSettings,
)
from openhands.agent_server.telemetry.policy import resolve


def test_schema_version_was_not_bumped_for_consent():
    assert PERSISTED_SETTINGS_SCHEMA_VERSION == 2


def test_there_is_no_typed_consent_field():
    assert "telemetry_consent" not in PersistedSettings.model_fields
    assert "telemetry_consent_updated_at" not in PersistedSettings.model_fields


@pytest.mark.parametrize("version", [1, 2])
def test_older_settings_still_load(version: int):
    settings = PersistedSettings.from_persisted(
        {"schema_version": version, "active_profile": "default"}
    )
    assert settings.schema_version == 2
    assert settings.active_profile == "default"
    # No consent recorded anywhere means no consent.
    assert resolve(settings.misc_settings, env={}).enabled is False


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
        PersistedSettings.from_persisted({"schema_version": 3})
