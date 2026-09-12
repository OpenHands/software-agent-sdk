"""Router for LLM model, provider, and subscription information endpoints."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, SecretStr

from openhands.agent_server._secrets_exposure import get_cipher, get_config
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk.llm import LLM
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


class BalanceResponse(BaseModel):
    """Credit balance information for the resolved LLM's provider."""

    provider: str
    limit: float | None = None
    limit_remaining: float | None = None
    usage: float
    usage_daily: float | None = None
    usage_weekly: float | None = None
    usage_monthly: float | None = None
    is_free_tier: bool = False


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
        verified_provider_models = set(VERIFIED_MODELS.get(provider, ()))
        for model in all_models:
            model_provider, _, _ = _extract_model_and_provider(model)
            if model_provider == provider or model in verified_provider_models:
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


OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
BALANCE_HTTP_TIMEOUT_SECONDS = 10.0


def _extract_api_key(llm: LLM) -> str | None:
    """Return the LLM's API key as plaintext, or ``None`` if unset."""
    key = llm.api_key
    if isinstance(key, SecretStr):
        key = key.get_secret_value()
    if key is None:
        return None
    key = str(key).strip()
    return key or None


async def _fetch_openrouter_balance(api_key: str) -> BalanceResponse:
    """Query OpenRouter's key endpoint for credit limit and usage information.

    See https://openrouter.ai/docs/api-reference/limits for the response shape.
    """
    try:
        async with httpx.AsyncClient(timeout=BALANCE_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(
                OPENROUTER_KEY_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"OpenRouter balance lookup failed with status {e.response.status_code}"
            ),
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenRouter balance lookup failed: {e}",
        )

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenRouter balance lookup returned an unexpected response",
        )

    return BalanceResponse(
        provider="openrouter",
        limit=data.get("limit"),
        limit_remaining=data.get("limit_remaining"),
        usage=data.get("usage", 0.0),
        usage_daily=data.get("usage_daily"),
        usage_weekly=data.get("usage_weekly"),
        usage_monthly=data.get("usage_monthly"),
        is_free_tier=bool(data.get("is_free_tier", False)),
    )


# Provider name -> balance fetcher taking the plaintext API key. Add new
# providers here as they gain balance/credits endpoints.
_BALANCE_FETCHERS: dict[str, Callable[[str], Awaitable[BalanceResponse]]] = {
    "openrouter": _fetch_openrouter_balance,
}


def _resolve_balance_provider(llm: LLM) -> str | None:
    """Map an LLM config to a provider supported by ``_BALANCE_FETCHERS``."""
    base_url = llm.base_url or ""
    if "openrouter.ai" in base_url or llm.model.startswith("openrouter/"):
        return "openrouter"
    return None


def _resolve_llm_for_balance(request: Request, profile: str | None) -> LLM:
    """Load the named profile's LLM, or the active agent settings LLM."""
    if profile is not None:
        store = get_llm_profile_store()
        try:
            return store.load(profile, cipher=get_cipher(request))
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile '{profile}' not found",
            )

    config = get_config(request)
    settings = get_settings_store(config).load() or PersistedSettings()
    return settings.agent_settings.llm


@llm_router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    request: Request,
    profile: str | None = Query(
        default=None,
        description=(
            "LLM profile name to check. Defaults to the currently active LLM settings."
        ),
    ),
) -> BalanceResponse:
    """Get the credit balance for the resolved LLM's provider.

    Currently only OpenRouter is supported (detected via an ``openrouter.ai``
    base URL or an ``openrouter/`` model prefix). The provider is queried
    server-side with the stored API key; the key itself is never returned.

    Returns 404 if the provider does not support balance lookup or no API key
    is configured, and 502 if the upstream provider call fails.
    """
    llm = _resolve_llm_for_balance(request, profile)
    provider = _resolve_balance_provider(llm)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Balance lookup is not supported for model '{llm.model}'. "
                "Supported providers: " + ", ".join(sorted(_BALANCE_FETCHERS))
            ),
        )

    api_key = _extract_api_key(llm)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No API key is configured for the resolved '{provider}' LLM; "
                "cannot look up balance"
            ),
        )

    return await _BALANCE_FETCHERS[provider](api_key)


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
    credentials = None
    try:
        credentials = await auth.poll_device_login(pending.device_code, persist=False)
    finally:
        async with _OPENAI_DEVICE_LOGIN_LOCK:
            _IN_FLIGHT_OPENAI_DEVICE_LOGINS.discard(request.device_code)
            # Keep the opaque poll token usable if the provider is still pending
            # or if the polling request fails before credentials are obtained.
            if credentials is None and pending.epoch == _OPENAI_DEVICE_LOGIN_EPOCH:
                _PENDING_OPENAI_DEVICE_LOGINS[request.device_code] = pending

    async with _OPENAI_DEVICE_LOGIN_LOCK:
        current_epoch = _OPENAI_DEVICE_LOGIN_EPOCH
        if credentials is None:
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
