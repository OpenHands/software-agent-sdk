"""Consent surface for product analytics.

This is a dedicated router rather than a field on ``PATCH /api/settings``, for
two reasons:

1. ``SettingsResponse`` / ``SettingsUpdateRequest`` live in ``openhands-sdk``,
   which stays vendor-neutral and free of telemetry concepts. Routing consent
   through them would push a telemetry concept into the core SDK.
2. Consent should be an explicit, auditable act. A deep-merged diff blob is the
   wrong shape for something that must be *granted*, not accidentally set.
"""

from typing import Final

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import get_settings_store
from openhands.agent_server.persistence.models import (
    PersistedSettings,
    SettingsUpdatePayload,
    TelemetryConsent,
)
from openhands.agent_server.telemetry.models import TELEMETRY_SCHEMA_VERSION
from openhands.agent_server.telemetry.policy import TelemetryMode, resolve
from openhands.agent_server.telemetry.service import notify_consent_changed
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

CONSENT_PATH: Final = "/consent"  # -> /api/telemetry/consent

telemetry_router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


class TelemetryConsentResponse(BaseModel):
    """The full telemetry state, so a UI can render it without guessing."""

    mode: TelemetryMode = Field(
        description="Deployment-supplied policy. Not user-changeable."
    )
    consent: TelemetryConsent = Field(
        description="The user's recorded choice. Only applies in local_opt_in."
    )
    effective_enabled: bool = Field(
        description="Whether events are actually being delivered right now."
    )
    is_locked: bool = Field(
        description=(
            "True when the user cannot change the outcome — either the "
            "deployment mandates telemetry, has disabled it, or an operator "
            "set DO_NOT_TRACK."
        )
    )
    reason: str = Field(description="Which rule decided effective_enabled.")
    schema_version: int = Field(
        default=TELEMETRY_SCHEMA_VERSION,
        description="Version of the diagnostic-event schema this server emits.",
    )


class TelemetryConsentUpdateRequest(BaseModel):
    consent: TelemetryConsent = Field(
        description="'granted' or 'denied'. 'unset' resets to no decision."
    )


def _build_response(mode: TelemetryMode, consent: TelemetryConsent):
    decision = resolve(mode, consent)
    return TelemetryConsentResponse(
        mode=mode,
        consent=consent,
        effective_enabled=decision.enabled,
        is_locked=decision.is_locked,
        reason=decision.reason,
    )


@telemetry_router.get(CONSENT_PATH, response_model=TelemetryConsentResponse)
async def get_telemetry_consent(request: Request) -> TelemetryConsentResponse:
    """Report the current telemetry policy and consent state."""
    config = get_config(request)
    store = get_settings_store(config)
    settings = store.load()
    consent: TelemetryConsent = (
        settings.telemetry_consent if settings is not None else "unset"
    )
    return _build_response(config.telemetry.mode, consent)


@telemetry_router.put(CONSENT_PATH, response_model=TelemetryConsentResponse)
async def set_telemetry_consent(
    request: Request,
    body: TelemetryConsentUpdateRequest,
) -> TelemetryConsentResponse:
    """Record a consent decision.

    Under ``cloud_locked`` the choice is still persisted (so it survives a move
    to a self-hosted deployment) but does not change delivery — the response
    reports ``is_locked`` so a UI can say "managed by your administrator"
    instead of having to special-case an error status.

    Revocation is propagated to the live sink synchronously, which stops
    delivery and **discards** anything already queued.
    """
    config = get_config(request)
    mode = config.telemetry.mode
    store = get_settings_store(config)

    payload: SettingsUpdatePayload = {"telemetry_consent": body.consent}

    def _apply(settings: PersistedSettings) -> PersistedSettings:
        settings.update(payload)
        return settings

    try:
        store.update(_apply)
    except RuntimeError:
        logger.error("Telemetry consent update blocked: settings file unreadable")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settings file is corrupted or encrypted with a different key",
        )
    except (OSError, PermissionError):
        logger.error("Telemetry consent update failed - file I/O error")
        raise HTTPException(
            status_code=500, detail="Failed to persist telemetry consent"
        )

    # Before returning, so a revoke-then-check cannot observe delivery.
    notify_consent_changed(body.consent)

    logger.info("Telemetry consent set to %s (mode=%s)", body.consent, mode)
    return _build_response(mode, body.consent)
