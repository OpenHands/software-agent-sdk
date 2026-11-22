"""Router for LLM model, provider, and subscription information endpoints."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, SecretStr

from openhands.agent_server._secrets_exposure import profile_key_mismatch
from openhands.agent_server.dependencies import get_cipher, get_llm_profile_store
from openhands.agent_server.errors import error_responses
from openhands.agent_server.persistence import run_store
from openhands.agent_server.upstream_models import (
    build_upstream_models_url,
    fetch_upstream_models,
)
from openhands.sdk.errors import (
    Conflict,
    Invalid,
    NotFound,
    UpstreamFailed,
    UpstreamTimeout,
)
from openhands.sdk.llm.auth.openai import (
    DEVICE_CODE_TIMEOUT_SECONDS,
    OPENAI_CODEX_MODELS,
    DeviceCode,
    OpenAISubscriptionAuth,
)
from openhands.sdk.llm.llm_profile_store import PROFILE_NAME_PATTERN, LLMProfileStore
from openhands.sdk.llm.utils.unverified_models import (
    _extract_model_and_provider,
    _get_litellm_provider_names,
    get_supported_llm_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import SecretDecryptionError


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


@contextmanager
def _openai_upstream_errors() -> Iterator[None]:
    """Report a failed call to auth.openai.com as a gateway error, not a 500."""
    try:
        yield
    except httpx.TimeoutException as exc:
        raise UpstreamTimeout("OpenAI did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise UpstreamFailed("OpenAI is unreachable") from exc
    except RuntimeError as exc:
        # The SDK raises RuntimeError for a non-2xx or malformed OpenAI answer.
        raise UpstreamFailed(f"OpenAI device login failed: {exc}") from exc


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
            if model_provider == provider or (
                model in verified_provider_models
                # A namespaced verified entry (an OpenRouter id with the
                # ``openrouter/`` prefix stripped, e.g. ``deepseek/deepseek-chat``)
                # can collide with another provider's real catalog model of the
                # same name. When it does, the model extracts to that other
                # provider, so only honor the verified-list match when the model
                # does not resolve to a different provider.
                and not ("/" in model and model_provider and model_provider != provider)
            ):
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
    "/models/upstream",
    response_model=ModelsResponse,
    responses=error_responses(
        {
            Invalid: "Invalid base_url, profile_name, or disallowed upstream target",
            NotFound: "Referenced LLM profile not found",
            Conflict: "Stored profile API key cannot be decrypted with current cipher",
            UpstreamFailed: "Upstream model catalog unreachable or invalid response",
            UpstreamTimeout: "Upstream model catalog request timed out",
        }
    ),
)
async def list_upstream_models(
    base_url: str | None = Query(
        default=None,
        description=(
            "OpenAI-compatible upstream base URL to query for available models "
            "(fetches GET {base_url}/v1/models)."
        ),
    ),
    profile_name: str | None = Query(
        default=None,
        description=(
            "Optional saved LLM profile name whose base_url and stored API key "
            "(or linked provider connection key) are used server-side."
        ),
    ),
    store: LLMProfileStore = Depends(get_llm_profile_store),
    cipher: Cipher | None = Depends(get_cipher),
) -> ModelsResponse:
    """Discover available models from an OpenAI-compatible custom ``base_url``.

    When ``profile_name`` is supplied, the server loads the stored profile
    (resolving any linked provider connection) and uses its API key server-side
    for authenticated catalog endpoints; secrets never leave the server.
    """
    cleaned_base_url = base_url.strip() if base_url is not None else None
    cleaned_base_url = cleaned_base_url or None
    cleaned_profile_name = profile_name.strip() if profile_name is not None else None
    cleaned_profile_name = cleaned_profile_name or None

    if not cleaned_base_url and not cleaned_profile_name:
        raise Invalid("Either base_url or profile_name must be provided")

    effective_base_url = cleaned_base_url
    api_key: str | None = None

    if cleaned_profile_name is not None:
        if not re.fullmatch(PROFILE_NAME_PATTERN, cleaned_profile_name):
            raise Invalid(f"Invalid profile name '{cleaned_profile_name}'")
        try:
            llm = await run_store(
                store.load, cleaned_profile_name, cipher=cipher, strict=True
            )
        except SecretDecryptionError as exc:
            raise profile_key_mismatch(cleaned_profile_name, exc)
        except FileNotFoundError:
            raise NotFound(f"Profile '{cleaned_profile_name}' not found")

        profile_base_url = llm.base_url
        if effective_base_url is None:
            if not profile_base_url:
                raise Invalid(
                    f"Profile '{cleaned_profile_name}' does not define a base_url"
                )
            effective_base_url = profile_base_url

        # Only attach the profile's stored credential when querying the
        # profile's own configured upstream endpoint, preventing credential
        # leakage to an arbitrary caller-supplied URL.
        should_attach_key = False
        if cleaned_base_url is None:
            should_attach_key = True
        elif profile_base_url:
            target_url = build_upstream_models_url(cleaned_base_url)
            try:
                profile_url = build_upstream_models_url(profile_base_url)
            except Invalid:
                profile_url = None
            should_attach_key = profile_url is not None and target_url == profile_url

        if should_attach_key and isinstance(llm.api_key, SecretStr):
            secret = llm.api_key.get_secret_value().strip()
            if secret:
                api_key = secret

    assert effective_base_url is not None
    models = await fetch_upstream_models(effective_base_url, api_key=api_key)
    return ModelsResponse(models=models)


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
    except (RuntimeError, httpx.HTTPError):
        # A rejected refresh (RuntimeError) or an unreachable auth.openai.com
        # (httpx transport error) both leave no usable login right now.
        return SubscriptionStatusResponse(connected=False)
    return _status_from_auth(auth)


@llm_router.post(
    "/subscription/openai/device/start",
    response_model=SubscriptionDeviceStartResponse,
)
async def start_openai_subscription_device_login() -> SubscriptionDeviceStartResponse:
    """Start ChatGPT device-code sign-in without returning tokens."""
    auth = _get_openai_subscription_auth()
    with _openai_upstream_errors():
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
            raise NotFound("Subscription device login not found or expired")
        _IN_FLIGHT_OPENAI_DEVICE_LOGINS.add(request.device_code)

    auth = _get_openai_subscription_auth()
    credentials = None
    try:
        with _openai_upstream_errors():
            credentials = await auth.poll_device_login(
                pending.device_code, persist=False
            )
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
