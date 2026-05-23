"""Router for LLM model, provider, and subscription information endpoints."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from openhands.sdk.llm.auth.openai import (
    DEVICE_CODE_TIMEOUT_SECONDS,
    OPENAI_CODEX_MODELS,
    DeviceCode,
    OpenAISubscriptionAuth,
)
from openhands.sdk.llm.utils.unverified_models import (
    _extract_model_and_provider,
    _get_litellm_provider_names,
    get_supported_llm_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS


llm_router = APIRouter(prefix="/llm", tags=["LLM"])


@dataclass(frozen=True)
class PendingDeviceLogin:
    """Server-side state for an in-progress device-code login."""

    device_code: DeviceCode
    expires_at: int
    epoch: int


_PENDING_OPENAI_DEVICE_LOGINS: dict[str, PendingDeviceLogin] = {}
_IN_FLIGHT_OPENAI_DEVICE_LOGINS: set[str] = set()
_OPENAI_DEVICE_LOGIN_LOCK = asyncio.Lock()
_OPENAI_DEVICE_LOGIN_EPOCH = 0


class ProvidersResponse(BaseModel):
    """Response containing the list of available LLM providers."""

    providers: list[str]


class ModelsResponse(BaseModel):
    """Response containing the list of available LLM models."""

    models: list[str]


class VerifiedModelsResponse(BaseModel):
    """Response containing verified LLM models organized by provider."""

    models: dict[str, list[str]]


class SubscriptionStatusResponse(BaseModel):
    """Safe subscription authentication status."""

    vendor: str = "openai"
    connected: bool
    account_email: str | None = None
    expires_at: int | None = None


class SubscriptionDeviceStartResponse(BaseModel):
    """Device-code challenge details for browser sign-in."""

    device_code: str = Field(description="Opaque server-side polling token.")
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_at: int
    interval_seconds: int


class SubscriptionDevicePollRequest(BaseModel):
    """Poll request for a previously-started subscription device login."""

    device_code: str


class SubscriptionModelsResponse(BaseModel):
    """Models available through a subscription provider."""

    vendor: str = "openai"
    models: list[str]


def _get_openai_subscription_auth() -> OpenAISubscriptionAuth:
    return OpenAISubscriptionAuth()


def _status_from_auth(auth: OpenAISubscriptionAuth) -> SubscriptionStatusResponse:
    creds = auth.get_credentials()
    if creds is None or creds.is_expired():
        return SubscriptionStatusResponse(connected=False)
    return SubscriptionStatusResponse(connected=True, expires_at=creds.expires_at)


def _drop_expired_device_logins() -> None:
    now = int(time.time() * 1000)
    for key, pending in list(_PENDING_OPENAI_DEVICE_LOGINS.items()):
        if pending.expires_at <= now:
            _PENDING_OPENAI_DEVICE_LOGINS.pop(key, None)


@llm_router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """List all available LLM providers supported by LiteLLM."""
    providers = sorted(_get_litellm_provider_names())
    return ProvidersResponse(providers=providers)


@llm_router.get("/models", response_model=ModelsResponse)
async def list_models(
    provider: str | None = Query(
        default=None,
        description="Filter models by provider (e.g., 'openai', 'anthropic')",
    ),
) -> ModelsResponse:
    """List all available LLM models supported by LiteLLM.

    Args:
        provider: Optional provider name to filter models by.

    Note: Bedrock models are excluded unless AWS credentials are configured.
    """
    all_models = get_supported_llm_models()

    if provider is None:
        models = sorted(set(all_models))
    else:
        filtered_models = []
        for model in all_models:
            model_provider, model_id, separator = _extract_model_and_provider(model)
            if model_provider == provider:
                filtered_models.append(model)
        models = sorted(set(filtered_models))

    return ModelsResponse(models=models)


@llm_router.get("/models/verified", response_model=VerifiedModelsResponse)
async def list_verified_models() -> VerifiedModelsResponse:
    """List all verified LLM models organized by provider.

    Verified models are those that have been tested and confirmed to work well
    with OpenHands.
    """
    return VerifiedModelsResponse(models=VERIFIED_MODELS)


@llm_router.get(
    "/subscription/openai/models", response_model=SubscriptionModelsResponse
)
async def list_openai_subscription_models() -> SubscriptionModelsResponse:
    """List models available through ChatGPT subscription authentication."""
    return SubscriptionModelsResponse(models=sorted(OPENAI_CODEX_MODELS))


@llm_router.get(
    "/subscription/openai/status", response_model=SubscriptionStatusResponse
)
async def get_openai_subscription_status() -> SubscriptionStatusResponse:
    """Return safe ChatGPT subscription connection state without tokens."""
    auth = _get_openai_subscription_auth()
    try:
        await auth.refresh_if_needed()
    except RuntimeError:
        return SubscriptionStatusResponse(connected=False)
    return _status_from_auth(auth)


@llm_router.post(
    "/subscription/openai/device/start",
    response_model=SubscriptionDeviceStartResponse,
)
async def start_openai_subscription_device_login() -> SubscriptionDeviceStartResponse:
    """Start ChatGPT device-code sign-in without returning tokens."""
    auth = _get_openai_subscription_auth()
    challenge = await auth.start_device_login()
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time() * 1000) + (DEVICE_CODE_TIMEOUT_SECONDS * 1000)
    async with _OPENAI_DEVICE_LOGIN_LOCK:
        _drop_expired_device_logins()
        _PENDING_OPENAI_DEVICE_LOGINS[token] = PendingDeviceLogin(
            device_code=challenge,
            expires_at=expires_at,
            epoch=_OPENAI_DEVICE_LOGIN_EPOCH,
        )
    return SubscriptionDeviceStartResponse(
        device_code=token,
        user_code=challenge.user_code,
        verification_uri=challenge.verification_url,
        expires_at=expires_at,
        interval_seconds=challenge.interval,
    )


@llm_router.post(
    "/subscription/openai/device/poll", response_model=SubscriptionStatusResponse
)
async def poll_openai_subscription_device_login(
    request: SubscriptionDevicePollRequest,
) -> SubscriptionStatusResponse:
    """Poll a ChatGPT device-code sign-in without returning tokens."""
    async with _OPENAI_DEVICE_LOGIN_LOCK:
        _drop_expired_device_logins()
        pending = _PENDING_OPENAI_DEVICE_LOGINS.pop(request.device_code, None)
        if pending is None:
            if request.device_code in _IN_FLIGHT_OPENAI_DEVICE_LOGINS:
                return SubscriptionStatusResponse(connected=False)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription device login not found or expired",
            )
        _IN_FLIGHT_OPENAI_DEVICE_LOGINS.add(request.device_code)

    auth = _get_openai_subscription_auth()
    try:
        credentials = await auth.poll_device_login(pending.device_code, persist=False)
    except BaseException:
        async with _OPENAI_DEVICE_LOGIN_LOCK:
            _IN_FLIGHT_OPENAI_DEVICE_LOGINS.discard(request.device_code)
            if pending.epoch == _OPENAI_DEVICE_LOGIN_EPOCH:
                _PENDING_OPENAI_DEVICE_LOGINS[request.device_code] = pending
        raise

    async with _OPENAI_DEVICE_LOGIN_LOCK:
        _IN_FLIGHT_OPENAI_DEVICE_LOGINS.discard(request.device_code)
        current_epoch = _OPENAI_DEVICE_LOGIN_EPOCH
        if credentials is None:
            if pending.epoch == current_epoch:
                _PENDING_OPENAI_DEVICE_LOGINS[request.device_code] = pending
            return SubscriptionStatusResponse(connected=False)
        if pending.epoch != current_epoch:
            return SubscriptionStatusResponse(connected=False)
        auth.save_credentials(credentials)
        return SubscriptionStatusResponse(
            connected=True, expires_at=credentials.expires_at
        )


@llm_router.post(
    "/subscription/openai/logout", response_model=SubscriptionStatusResponse
)
async def logout_openai_subscription() -> SubscriptionStatusResponse:
    """Remove stored ChatGPT subscription credentials."""
    global _OPENAI_DEVICE_LOGIN_EPOCH

    auth = _get_openai_subscription_auth()
    async with _OPENAI_DEVICE_LOGIN_LOCK:
        _OPENAI_DEVICE_LOGIN_EPOCH += 1
        _PENDING_OPENAI_DEVICE_LOGINS.clear()
        auth.logout()
    return SubscriptionStatusResponse(connected=False)
