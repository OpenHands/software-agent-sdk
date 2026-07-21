"""Consent resolution.

There is no deployment mode axis. Effective consent is resolved from
``misc_settings.telemetry.consent``, the legacy Canvas key, and the environment.
"""

import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.telemetry.policy import (
    CONSENT_ENV,
    CONSENT_MODE_ENV,
    DO_NOT_TRACK_ENV,
    kill_switch_engaged,
    read_consent,
    read_legacy_consent,
    resolve,
)


def misc(consent=None, managed=None, legacy=None):
    out: dict = {}
    section: dict = {}
    if consent is not None:
        section["consent"] = consent
    if managed is not None:
        section["managed"] = managed
    if section:
        out["telemetry"] = section
    if legacy is not None:
        out["app_preferences"] = {"user_consents_to_analytics": legacy}
    return out


# ── defaults ──────────────────────────────────────────────────────────────


def test_no_settings_means_no_consent():
    d = resolve(None, env={})
    assert d.consent == "unset"
    assert d.enabled is False
    assert d.reason == "default"


def test_telemetry_is_disabled_by_default_in_config():
    """Library and headless consumers get no exporter at all."""
    assert Config().telemetry.exporter == "none"


@pytest.mark.parametrize("value", [None, {}, {"telemetry": {}}, {"telemetry": "junk"}])
def test_absent_or_malformed_namespace_is_not_consent(value):
    assert resolve(value, env={}).enabled is False


@pytest.mark.parametrize("value", ["maybe", "", 42, [], {}])
def test_malformed_consent_value_is_ignored(value):
    assert resolve(misc(consent=value), env={}).consent == "unset"


# ── settings ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "consent,enabled", [("granted", True), ("denied", False), ("unset", False)]
)
def test_settings_consent_decides(consent, enabled):
    d = resolve(misc(consent=consent), env={})
    assert d.enabled is enabled
    if consent != "unset":
        assert d.reason == "settings"


def test_managed_flag_marks_the_choice_as_locked():
    d = resolve(misc(consent="granted", managed=True), env={})
    assert d.enabled is True
    assert d.is_locked is True
    assert resolve(misc(consent="granted"), env={}).is_locked is False


def test_read_consent_returns_none_when_absent():
    assert read_consent(None) == (None, False)
    assert read_consent({}) == (None, False)
    assert read_consent(misc(consent="granted")) == ("granted", False)
    assert read_consent(misc(consent="granted", managed=True)) == ("granted", True)


# ── legacy key ────────────────────────────────────────────────────────────


def test_legacy_key_is_read_when_the_new_namespace_is_absent():
    """An existing Canvas user must not be silently reset to no-consent."""
    d = resolve(misc(legacy=True), env={})
    assert d.enabled is True
    assert d.reason == "legacy_settings"

    assert resolve(misc(legacy=False), env={}).enabled is False


def test_new_namespace_wins_over_the_legacy_key():
    d = resolve(misc(consent="denied", legacy=True), env={})
    assert d.enabled is False
    assert d.reason == "settings"


def test_read_legacy_consent_accepts_the_boolean_shape():
    assert read_legacy_consent(misc(legacy=True)) == "granted"
    assert read_legacy_consent(misc(legacy=False)) == "denied"
    assert read_legacy_consent({}) is None


# ── environment ───────────────────────────────────────────────────────────


def test_env_seed_applies_only_when_settings_are_unset():
    env = {CONSENT_ENV: "granted"}

    seeded = resolve(None, env=env)
    assert seeded.enabled is True
    assert seeded.reason == "env_seed"

    # An explicit user choice wins over a seed.
    chosen = resolve(misc(consent="denied"), env=env)
    assert chosen.enabled is False
    assert chosen.reason == "settings"


def test_seed_is_the_default_mode():
    """An operator default must not silently overrule an explicit choice."""
    env = {CONSENT_ENV: "granted", CONSENT_MODE_ENV: "seed"}
    assert resolve(misc(consent="denied"), env=env).enabled is False
    # Same without naming the mode.
    assert (
        resolve(misc(consent="denied"), env={CONSENT_ENV: "granted"}).enabled is False
    )


def test_env_override_beats_settings():
    env = {CONSENT_ENV: "granted", CONSENT_MODE_ENV: "override"}
    d = resolve(misc(consent="denied"), env=env)
    assert d.enabled is True
    assert d.reason == "env_override"
    assert d.is_locked is True


def test_env_override_can_also_force_denial():
    env = {CONSENT_ENV: "denied", CONSENT_MODE_ENV: "override"}
    assert resolve(misc(consent="granted"), env=env).enabled is False


def test_unrecognised_mode_falls_back_to_seed():
    env = {CONSENT_ENV: "granted", CONSENT_MODE_ENV: "nonsense"}
    assert resolve(misc(consent="denied"), env=env).enabled is False


# ── kill switch ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_do_not_track_overrides_everything(value):
    env = {
        DO_NOT_TRACK_ENV: value,
        CONSENT_ENV: "granted",
        CONSENT_MODE_ENV: "override",
    }
    d = resolve(misc(consent="granted", managed=True), env=env)
    assert d.enabled is False
    assert d.reason == "kill_switch"
    assert d.is_locked is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off"])
def test_do_not_track_ignores_falsey_values(value):
    assert kill_switch_engaged({DO_NOT_TRACK_ENV: value}) is False
    assert resolve(misc(consent="granted"), env={DO_NOT_TRACK_ENV: value}).enabled


def test_the_redundant_alias_is_gone():
    """OH_TELEMETRY_DISABLED was dropped in favour of DO_NOT_TRACK alone."""
    assert kill_switch_engaged({"OH_TELEMETRY_DISABLED": "1"}) is False
