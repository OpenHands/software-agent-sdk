"""Resolution of *whether* telemetry may be delivered.

The policy is deliberately split across two owners, because the two questions
have different authorities:

* **Mode** is supplied by the deployment (``Config.telemetry.mode``). A hosted
  deployment can require telemetry; an end user cannot turn that off.
* **Consent** is supplied by the person using a local installation
  (``PersistedSettings.telemetry_consent``) and only matters in
  ``local_opt_in``.

Consent is *not* read from ``misc_settings``: that container is documented as
opaque and never interpreted by the agent-server, and this value is very much
interpreted by the agent-server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


TelemetryMode = Literal["cloud_locked", "local_opt_in", "disabled"]
TelemetryConsent = Literal["granted", "denied", "unset"]

DO_NOT_TRACK_ENV = "DO_NOT_TRACK"
TELEMETRY_DISABLED_ENV = "OH_TELEMETRY_DISABLED"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class TelemetryDecision:
    """The resolved answer, with the reason retained for the consent API."""

    enabled: bool
    reason: Literal[
        "kill_switch",
        "mode_disabled",
        "cloud_locked",
        "consent_granted",
        "consent_denied",
        "consent_unset",
    ]

    @property
    def is_locked(self) -> bool:
        """True when the user cannot change the outcome from settings."""
        return self.reason in ("kill_switch", "mode_disabled", "cloud_locked")


def kill_switch_engaged(env: dict[str, str] | None = None) -> bool:
    """Whether an operator has forced telemetry off.

    This overrides ``cloud_locked``. The Cloud-required policy is about end
    users, not about the operator running the process — an operator who sets
    ``DO_NOT_TRACK=1`` has made a deliberate deployment-level decision, and
    honouring the standard variable is the difference between an opt-out and a
    dark pattern.
    """
    source = os.environ if env is None else env
    for name in (DO_NOT_TRACK_ENV, TELEMETRY_DISABLED_ENV):
        value = source.get(name)
        if value is not None and value.strip().lower() in _TRUTHY:
            return True
    return False


def resolve(
    mode: TelemetryMode,
    consent: TelemetryConsent,
    *,
    env: dict[str, str] | None = None,
) -> TelemetryDecision:
    """Resolve mode + consent into a single yes/no.

    Ordering matters: the kill switch is checked before anything else so it
    cannot be outranked by ``cloud_locked``.
    """
    if kill_switch_engaged(env):
        return TelemetryDecision(enabled=False, reason="kill_switch")

    if mode == "disabled":
        return TelemetryDecision(enabled=False, reason="mode_disabled")

    if mode == "cloud_locked":
        return TelemetryDecision(enabled=True, reason="cloud_locked")

    if consent == "granted":
        return TelemetryDecision(enabled=True, reason="consent_granted")
    if consent == "denied":
        return TelemetryDecision(enabled=False, reason="consent_denied")
    return TelemetryDecision(enabled=False, reason="consent_unset")
