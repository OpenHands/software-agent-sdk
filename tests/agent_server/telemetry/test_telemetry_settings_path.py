"""Consent is written through ``PATCH /api/settings``.

There is no dedicated telemetry consent endpoint. Canvas writes
``misc_settings.telemetry.consent`` like any other frontend preference, and the
settings-update path re-resolves the telemetry decision before returning.
"""

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.persistence.store import get_settings_store
from openhands.agent_server.telemetry.policy import TelemetryDecision, resolve


SETTINGS_URL = "/api/settings"


def diff(consent: str) -> dict:
    return {"misc_settings_diff": {"telemetry": {"consent": consent}}}


@pytest.fixture
def client(config_factory):
    return TestClient(create_app(config_factory()))


class RecordingSink:
    """Stands in for the live sink to observe propagation."""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.events: list = []
        self.decisions: list[TelemetryDecision] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, event):
        self.events.append(event)

    def on_decision_changed(self, decision: TelemetryDecision) -> None:
        self.decisions.append(decision)
        self._enabled = decision.enabled
        if not decision.enabled:
            self.events.clear()

    async def aclose(self):
        pass


# ── persistence ───────────────────────────────────────────────────────────


def test_consent_is_written_and_read_through_misc_settings(client, config_factory):
    with client as c:
        assert c.patch(SETTINGS_URL, json=diff("granted")).status_code == 200
        body = c.get(SETTINGS_URL).json()

    assert body["misc_settings"]["telemetry"]["consent"] == "granted"

    settings = get_settings_store(config_factory()).load()
    assert settings is not None
    assert resolve(settings.misc_settings, env={}).enabled is True


def test_consent_survives_a_store_reset(client, config_factory, temp_persistence_dir):
    from openhands.agent_server.persistence.store import reset_stores

    with client as c:
        c.patch(SETTINGS_URL, json=diff("granted"))

    reset_stores()
    settings = get_settings_store(config_factory()).load()
    assert settings is not None
    assert resolve(settings.misc_settings, env={}).enabled is True


def test_unrelated_misc_settings_are_preserved(client):
    with client as c:
        c.patch(SETTINGS_URL, json={"misc_settings_diff": {"theme": "dark"}})
        c.patch(SETTINGS_URL, json=diff("granted"))
        body = c.get(SETTINGS_URL).json()

    assert body["misc_settings"]["theme"] == "dark"
    assert body["misc_settings"]["telemetry"]["consent"] == "granted"


def test_consent_does_not_appear_as_a_typed_settings_field(client):
    with client as c:
        body = c.patch(SETTINGS_URL, json=diff("granted")).json()
    assert "telemetry_consent" not in body


# ── propagation to the live sink ──────────────────────────────────────────


def test_granting_consent_notifies_the_sink(client, monkeypatch):
    import openhands.agent_server.telemetry.service as service_mod

    sink = RecordingSink(enabled=False)
    with client as c:
        # After startup: the lifespan builds its own sink and would clobber this.
        monkeypatch.setattr(service_mod, "_telemetry_sink", sink)
        assert c.patch(SETTINGS_URL, json=diff("granted")).status_code == 200

    assert sink.decisions, "settings write did not re-resolve the decision"
    assert sink.decisions[-1].enabled is True
    assert sink.enabled is True


def test_revoking_consent_discards_queued_events(client, monkeypatch):
    """The unchanged acceptance criterion, now via the settings path."""
    import openhands.agent_server.telemetry.service as service_mod

    sink = RecordingSink(enabled=True)
    sink.events.extend(["queued-1", "queued-2", "queued-3"])
    with client as c:
        monkeypatch.setattr(service_mod, "_telemetry_sink", sink)
        assert c.patch(SETTINGS_URL, json=diff("denied")).status_code == 200

    assert sink.decisions[-1].enabled is False
    assert sink.enabled is False
    assert sink.events == [], "revocation did not discard the queue"


def test_an_unrelated_settings_write_still_re_resolves(client, monkeypatch):
    """A misc write that does not touch telemetry is harmless but re-resolved."""
    import openhands.agent_server.telemetry.service as service_mod

    sink = RecordingSink(enabled=False)
    with client as c:
        monkeypatch.setattr(service_mod, "_telemetry_sink", sink)
        c.patch(SETTINGS_URL, json={"misc_settings_diff": {"theme": "dark"}})

    assert sink.decisions[-1].enabled is False


def test_a_non_misc_settings_write_does_not_touch_telemetry(client, monkeypatch):
    import openhands.agent_server.telemetry.service as service_mod

    sink = RecordingSink(enabled=True)
    with client as c:
        monkeypatch.setattr(service_mod, "_telemetry_sink", sink)
        c.patch(
            SETTINGS_URL,
            json={"conversation_settings_diff": {"max_iterations": 42}},
        )

    assert sink.decisions == []
    assert sink.enabled is True


def test_there_is_no_dedicated_consent_endpoint(client):
    with client as c:
        assert c.get("/api/telemetry/consent").status_code == 404
