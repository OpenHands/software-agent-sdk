"""Backward-compatible migration of persisted settings to schema v3."""

import pytest

from openhands.agent_server.persistence.models import (
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    PersistedSettings,
)


def test_current_schema_version_is_three():
    assert PERSISTED_SETTINGS_SCHEMA_VERSION == 3


@pytest.mark.parametrize("version", [1, 2])
def test_older_settings_load_without_consent(version: int):
    """An upgrade must never be mistaken for an opt-in."""
    settings = PersistedSettings.from_persisted(
        {"schema_version": version, "active_profile": "default"}
    )

    assert settings.telemetry_consent == "unset"
    assert settings.telemetry_consent_updated_at is None
    assert settings.schema_version == 3
    # Pre-existing data is preserved.
    assert settings.active_profile == "default"


def test_v2_misc_settings_are_preserved_across_the_migration():
    settings = PersistedSettings.from_persisted(
        {"schema_version": 2, "misc_settings": {"theme": "dark"}}
    )
    assert settings.misc_settings == {"theme": "dark"}
    assert settings.telemetry_consent == "unset"


def test_a_legacy_analytics_key_in_misc_settings_is_not_read_as_consent():
    """The opaque container must not be able to grant consent implicitly."""
    settings = PersistedSettings.from_persisted(
        {
            "schema_version": 2,
            "misc_settings": {
                "analytics_consent": True,
                "telemetry_consent": "granted",
            },
        }
    )
    assert settings.telemetry_consent == "unset"


def test_v3_settings_round_trip():
    original = PersistedSettings.from_persisted(
        {"schema_version": 3, "telemetry_consent": "granted"}
    )
    assert original.telemetry_consent == "granted"

    reloaded = PersistedSettings.from_persisted(original.model_dump(mode="json"))
    assert reloaded.telemetry_consent == "granted"


def test_a_newer_schema_version_is_still_rejected():
    with pytest.raises(ValueError, match="newer than supported"):
        PersistedSettings.from_persisted({"schema_version": 4})


def test_update_records_a_timestamp_only_when_the_value_changes():
    settings = PersistedSettings()
    assert settings.telemetry_consent_updated_at is None

    settings.update({"telemetry_consent": "granted"})
    first = settings.telemetry_consent_updated_at
    assert settings.telemetry_consent == "granted"
    assert first is not None

    # Re-applying the same value must not churn the timestamp.
    settings.update({"telemetry_consent": "granted"})
    assert settings.telemetry_consent_updated_at == first

    settings.update({"telemetry_consent": "denied"})
    assert settings.telemetry_consent == "denied"
    assert settings.telemetry_consent_updated_at != first


def test_update_leaves_consent_alone_when_not_supplied():
    settings = PersistedSettings()
    settings.update({"telemetry_consent": "granted"})
    settings.update({"misc_settings_diff": {"theme": "dark"}})

    assert settings.telemetry_consent == "granted"
    assert settings.misc_settings == {"theme": "dark"}
