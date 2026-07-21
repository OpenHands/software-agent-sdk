"""Resolution of *whether* telemetry may be delivered.

There is no deployment "mode" and no notion of Cloud here. The agent-server
resolves one thing — **effective consent** — and delivery additionally requires
that an exporter is configured.

Consent lives in ``misc_settings.telemetry.consent``. That container is
otherwise opaque to the agent-server, and this namespace is the single
sanctioned exception: the frontend owns the value, the agent-server only reads
it, and the shape is documented here rather than inferred.

A hosted deployment enforces its policy by *seeding* those misc settings before
any conversation starts (``consent = granted``, ``managed = true``), not by the
agent-server special-casing it.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal


TelemetryConsent = Literal["granted", "denied", "unset"]
"""The user's recorded choice. ``unset`` is the default and is not consent."""

ConsentMode = Literal["seed", "override"]
"""How ``OH_TELEMETRY_CONSENT`` interacts with the persisted value."""

#: The one namespace inside the otherwise-opaque ``misc_settings`` container
#: that the agent-server reads. Owned by the frontend, documented here.
MISC_NAMESPACE: Final = "telemetry"
MISC_CONSENT_KEY: Final = "consent"
MISC_MANAGED_KEY: Final = "managed"

#: Pre-namespace key Canvas used. Read as a fallback so an existing user is not
#: silently reset to no-consent; never written back.
LEGACY_MISC_NAMESPACE: Final = "app_preferences"
LEGACY_CONSENT_KEY: Final = "user_consents_to_analytics"

DO_NOT_TRACK_ENV: Final = "DO_NOT_TRACK"
CONSENT_ENV: Final = "OH_TELEMETRY_CONSENT"
CONSENT_MODE_ENV: Final = "OH_TELEMETRY_CONSENT_MODE"

_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})
_VALID_CONSENT: Final = frozenset({"granted", "denied", "unset"})


@dataclass(frozen=True, slots=True)
class TelemetryDecision:
    """The resolved answer, with provenance retained for logging and the API."""

    consent: TelemetryConsent
    enabled: bool
    reason: Literal[
        "kill_switch",
        "env_override",
        "settings",
        "legacy_settings",
        "env_seed",
        "default",
    ]
    managed: bool = False

    @property
    def is_locked(self) -> bool:
        """True when the user cannot change the outcome from settings."""
        return self.managed or self.reason in ("kill_switch", "env_override")


def kill_switch_engaged(env: Mapping[str, str] | None = None) -> bool:
    """Whether an operator has forced telemetry off via ``DO_NOT_TRACK``."""
    source = os.environ if env is None else env
    value = source.get(DO_NOT_TRACK_ENV)
    return value is not None and value.strip().lower() in _TRUTHY


def _coerce_consent(value: Any) -> TelemetryConsent | None:
    """Accept the documented strings, plus the legacy boolean shape."""
    if isinstance(value, bool):
        return "granted" if value else "denied"
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _VALID_CONSENT:
            return candidate  # type: ignore[return-value]
    return None


def _namespace(misc_settings: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    if not isinstance(misc_settings, Mapping):
        return {}
    section = misc_settings.get(name)
    return section if isinstance(section, Mapping) else {}


def read_consent(
    misc_settings: Mapping[str, Any] | None,
) -> tuple[TelemetryConsent | None, bool]:
    """Read ``misc_settings.telemetry`` -> ``(consent, managed)``.

    Returns ``None`` for consent when the namespace is absent or malformed, so
    the caller can fall back to the legacy key or an env seed rather than
    treating a broken value as a decision.
    """
    section = _namespace(misc_settings, MISC_NAMESPACE)
    consent = _coerce_consent(section.get(MISC_CONSENT_KEY))
    managed = bool(section.get(MISC_MANAGED_KEY) is True)
    return consent, managed


def read_legacy_consent(
    misc_settings: Mapping[str, Any] | None,
) -> TelemetryConsent | None:
    """Read the pre-namespace Canvas key. Fallback only; never written."""
    section = _namespace(misc_settings, LEGACY_MISC_NAMESPACE)
    return _coerce_consent(section.get(LEGACY_CONSENT_KEY))


def _env_consent(
    env: Mapping[str, str],
) -> tuple[TelemetryConsent | None, ConsentMode]:
    consent = _coerce_consent(env.get(CONSENT_ENV))
    raw_mode = (env.get(CONSENT_MODE_ENV) or "").strip().lower()
    # Default to `seed`: an operator default must not silently overrule a
    # choice the user has already made.
    mode: ConsentMode = "override" if raw_mode == "override" else "seed"
    return consent, mode


def resolve(
    misc_settings: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> TelemetryDecision:
    """Resolve effective consent from settings and environment.

    Precedence: kill switch, then an env *override*, then the persisted value,
    then the legacy key, then an env *seed*, then ``unset``.
    """
    source = os.environ if env is None else env

    if kill_switch_engaged(source):
        return TelemetryDecision(consent="denied", enabled=False, reason="kill_switch")

    env_value, env_mode = _env_consent(source)
    if env_value is not None and env_mode == "override":
        return TelemetryDecision(
            consent=env_value,
            enabled=env_value == "granted",
            reason="env_override",
        )

    settings_value, managed = read_consent(misc_settings)
    if settings_value is not None and settings_value != "unset":
        return TelemetryDecision(
            consent=settings_value,
            enabled=settings_value == "granted",
            reason="settings",
            managed=managed,
        )

    legacy_value = read_legacy_consent(misc_settings)
    if legacy_value is not None and legacy_value != "unset":
        return TelemetryDecision(
            consent=legacy_value,
            enabled=legacy_value == "granted",
            reason="legacy_settings",
            managed=managed,
        )

    if env_value is not None:
        return TelemetryDecision(
            consent=env_value,
            enabled=env_value == "granted",
            reason="env_seed",
            managed=managed,
        )

    return TelemetryDecision(
        consent="unset", enabled=False, reason="default", managed=managed
    )


__all__ = [
    "CONSENT_ENV",
    "CONSENT_MODE_ENV",
    "DO_NOT_TRACK_ENV",
    "LEGACY_CONSENT_KEY",
    "LEGACY_MISC_NAMESPACE",
    "MISC_CONSENT_KEY",
    "MISC_MANAGED_KEY",
    "MISC_NAMESPACE",
    "ConsentMode",
    "TelemetryConsent",
    "TelemetryDecision",
    "kill_switch_engaged",
    "read_consent",
    "read_legacy_consent",
    "resolve",
]
