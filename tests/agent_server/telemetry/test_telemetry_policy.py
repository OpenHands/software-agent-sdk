"""Policy resolution, including the operator kill switch."""

import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.telemetry.policy import (
    DO_NOT_TRACK_ENV,
    TELEMETRY_DISABLED_ENV,
    kill_switch_engaged,
    resolve,
)


TRUTH_TABLE = [
    # (mode, consent, expected_enabled)
    ("disabled", "granted", False),
    ("disabled", "denied", False),
    ("disabled", "unset", False),
    ("cloud_locked", "granted", True),
    ("cloud_locked", "denied", True),
    ("cloud_locked", "unset", True),
    ("local_opt_in", "granted", True),
    ("local_opt_in", "denied", False),
    ("local_opt_in", "unset", False),
]


@pytest.mark.parametrize("mode,consent,expected", TRUTH_TABLE)
def test_resolution_truth_table(mode, consent, expected):
    assert resolve(mode, consent, env={}).enabled is expected


def test_silence_is_not_consent():
    """A fresh local install must not emit before an explicit decision."""
    assert resolve("local_opt_in", "unset", env={}).enabled is False
    assert resolve("local_opt_in", "unset", env={}).reason == "consent_unset"


def test_telemetry_is_disabled_by_default():
    """The default Config — what libraries and headless consumers get."""
    assert Config().telemetry.mode == "disabled"
    assert resolve(Config().telemetry.mode, "granted", env={}).enabled is False


@pytest.mark.parametrize("var", [DO_NOT_TRACK_ENV, TELEMETRY_DISABLED_ENV])
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
@pytest.mark.parametrize("mode", ["cloud_locked", "local_opt_in"])
def test_kill_switch_overrides_every_mode(var, value, mode):
    """Explicitly including cloud_locked: an operator retains a break-glass."""
    env = {var: value}
    decision = resolve(mode, "granted", env=env)
    assert decision.enabled is False
    assert decision.reason == "kill_switch"


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off"])
def test_kill_switch_ignores_falsey_values(value):
    assert kill_switch_engaged({DO_NOT_TRACK_ENV: value}) is False
    assert resolve("cloud_locked", "unset", env={DO_NOT_TRACK_ENV: value}).enabled


def test_locked_flag_tells_a_ui_whether_the_choice_is_the_users():
    assert resolve("cloud_locked", "unset", env={}).is_locked is True
    assert resolve("disabled", "granted", env={}).is_locked is True
    assert resolve("local_opt_in", "granted", env={}).is_locked is False
    assert resolve("local_opt_in", "denied", env={}).is_locked is False
