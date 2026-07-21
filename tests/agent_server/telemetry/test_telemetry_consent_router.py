"""The consent HTTP surface and its persistence."""

import json

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.persistence.store import get_settings_store, reset_stores


CONSENT_URL = "/api/telemetry/consent"


@pytest.fixture
def client(config_factory):
    def _make(mode: str = "local_opt_in"):
        return TestClient(create_app(config_factory(mode)))

    return _make


def test_consent_defaults_to_unset_and_disabled(client):
    with client("local_opt_in") as c:
        body = c.get(CONSENT_URL).json()

    assert body["consent"] == "unset"
    assert body["effective_enabled"] is False
    assert body["is_locked"] is False
    assert body["schema_version"] >= 1


def test_granting_consent_enables_and_persists(client, temp_persistence_dir):
    with client("local_opt_in") as c:
        put = c.put(CONSENT_URL, json={"consent": "granted"})
        assert put.status_code == 200
        assert put.json()["effective_enabled"] is True

        assert c.get(CONSENT_URL).json()["consent"] == "granted"

    # Survives a store reset, i.e. it really is on disk.
    reset_stores()
    persisted = json.loads((temp_persistence_dir / "settings.json").read_text())
    assert persisted["telemetry_consent"] == "granted"
    assert persisted["telemetry_consent_updated_at"] is not None


def test_denying_consent_disables(client):
    with client("local_opt_in") as c:
        c.put(CONSENT_URL, json={"consent": "granted"})
        body = c.put(CONSENT_URL, json={"consent": "denied"}).json()

    assert body["consent"] == "denied"
    assert body["effective_enabled"] is False


def test_cloud_locked_records_the_choice_but_stays_enabled(client):
    """A UI can then say 'managed by your administrator' without a 4xx."""
    with client("cloud_locked") as c:
        body = c.put(CONSENT_URL, json={"consent": "denied"}).json()

    assert body["consent"] == "denied"
    assert body["effective_enabled"] is True
    assert body["is_locked"] is True
    assert body["reason"] == "cloud_locked"


def test_disabled_mode_stays_off_even_when_granted(client):
    with client("disabled") as c:
        body = c.put(CONSENT_URL, json={"consent": "granted"}).json()

    assert body["effective_enabled"] is False
    assert body["is_locked"] is True


def test_kill_switch_is_reflected_in_the_response(client, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    with client("cloud_locked") as c:
        body = c.get(CONSENT_URL).json()

    assert body["effective_enabled"] is False
    assert body["reason"] == "kill_switch"


def test_invalid_consent_value_is_rejected(client):
    with client("local_opt_in") as c:
        assert c.put(CONSENT_URL, json={"consent": "maybe"}).status_code == 422


def test_consent_does_not_land_in_misc_settings(client, config_factory):
    """misc_settings is documented as never interpreted by the server.

    Consent must live in its own typed field, not smuggled into the opaque
    container.
    """
    with client("local_opt_in") as c:
        c.put(CONSENT_URL, json={"consent": "granted"})

    settings = get_settings_store(config_factory("local_opt_in")).load()
    assert settings is not None
    assert settings.telemetry_consent == "granted"
    assert settings.misc_settings == {}


def test_consent_survives_an_unrelated_settings_patch(client):
    with client("local_opt_in") as c:
        c.put(CONSENT_URL, json={"consent": "granted"})
        c.patch("/api/settings", json={"misc_settings_diff": {"theme": "dark"}})

        assert c.get(CONSENT_URL).json()["consent"] == "granted"
