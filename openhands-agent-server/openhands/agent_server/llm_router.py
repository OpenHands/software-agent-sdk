"""Router for LLM model, provider, and subscription information endpoints."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
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


# --- OpenHands LiteLLM proxy discovery -----------------------------------
#
# Powers the provider=openhands connection form in Agent Canvas
# (see OpenHands/OpenHands#17810). Users type the OpenHands account URL they
# log in at (e.g. https://app.all-hands.dev, https://openhands.mycorp.example);
# this endpoint resolves that to the LiteLLM proxy URL by asking the account's
# own WebClientConfig, then optionally probing the proxy's health endpoint so
# a misconfigured URL is caught before the user burns a conversation on it.
#
# Never raises. Every failure mode surfaces as a structured error code so the
# frontend can render each row of the fallback matrix with actionable copy
# instead of a bare LiteLLM 405.

DISCOVERY_TIMEOUT_SECONDS = 10.0

# Error codes returned in the ``error`` field, one per fallback-matrix row.
ERR_OLD_OPENHANDS_INSTALL = "old_openhands_install"
ERR_NOT_OPENHANDS_INSTALL = "not_openhands_install"
ERR_INVALID_URL = "invalid_url"
ERR_TIMEOUT = "timeout"
ERR_NETWORK_ERROR = "network_error"
ERR_PROXY_LOOKS_LIKE_PRODUCT_URL = "proxy_looks_like_product_url"
ERR_PROXY_PROBE_FAILED = "proxy_probe_failed"


class DiscoverOpenHandsProxyRequest(BaseModel):
    """Client-supplied OpenHands account URL to resolve to a LiteLLM proxy."""

    app_url: str = Field(
        description=(
            "The user's OpenHands account URL — the URL they log in at "
            "(e.g. https://app.all-hands.dev or https://openhands.mycorp.example). "
            "Not the LiteLLM proxy URL; the whole point of this endpoint is to "
            "derive that."
        ),
    )


class DiscoverOpenHandsProxyResponse(BaseModel):
    """Result of resolving an OpenHands account URL to a LiteLLM proxy.

    ``verified=True`` means the LiteLLM proxy responded successfully to a
    liveliness probe, so the frontend can lock the derived ``base_url`` into
    the connection form with confidence. Any other outcome — including
    "proxy URL known but probe failed" — surfaces via ``error`` so each
    fallback-matrix row gets its own copy on the UI.
    """

    app_url: str
    llm_proxy_base_url: str | None = None
    verified: bool = False
    error: str | None = None
    error_message: str | None = Field(
        default=None,
        description=(
            "Human-readable elaboration of ``error``. The frontend is free to "
            "override with its own localized copy, but the message is useful "
            "as a fallback and in logs."
        ),
    )


def _normalize_url(raw: str) -> str | None:
    """Best-effort normalization of a user-typed URL.

    - Strips whitespace and trailing slashes.
    - Adds ``https://`` when the scheme is missing (users copy-paste hostnames).
    - Rejects everything that doesn't parse to an ``http`` or ``https`` URL
      with a host, so garbage never reaches the outbound request.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # Whitespace inside the host is only ever a user-typed typo (urlparse won't
    # reject it), so bounce it here rather than letting the DNS layer decide.
    if any(ch.isspace() for ch in parsed.netloc):
        return None
    # Drop any trailing slash on the path so join operations behave.
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _looks_like_web_client_config(payload: object) -> bool:
    """Cheap shape check: is this response actually a ``WebClientConfig``?

    ``app_mode`` is present on both SaaS and OHE (see the enterprise
    ``WebClientConfig`` model) and is the field with the least chance of being
    an accidental collision with an unrelated JSON endpoint.
    """
    return isinstance(payload, dict) and "app_mode" in payload


async def _probe_liveliness(
    client: httpx.AsyncClient, proxy_base_url: str
) -> tuple[bool, str | None, str | None]:
    """Probe ``<proxy_base_url>/health/liveliness`` — unauth, cheap.

    Returns ``(ok, error_code, error_message)``.
    A 405 specifically means the URL routes to a chat product, not a LiteLLM
    proxy — the exact #17803 symptom — so it gets its own code.
    """
    probe_url = proxy_base_url.rstrip("/") + "/health/liveliness"
    try:
        response = await client.get(probe_url)
    except httpx.TimeoutException:
        return False, ERR_TIMEOUT, f"Timed out probing {probe_url}."
    except httpx.HTTPError as exc:
        return (
            False,
            ERR_NETWORK_ERROR,
            f"Network error probing {probe_url}: {exc}",
        )

    if response.status_code == 200:
        return True, None, None
    if response.status_code == 405:
        return (
            False,
            ERR_PROXY_LOOKS_LIKE_PRODUCT_URL,
            (
                f"{proxy_base_url} responded 405 Method Not Allowed to "
                "GET /health/liveliness. This URL looks like an OpenHands "
                "product URL, not the LiteLLM proxy."
            ),
        )
    return (
        False,
        ERR_PROXY_PROBE_FAILED,
        (
            f"{probe_url} responded {response.status_code}, expected 200. "
            "The LiteLLM proxy is unreachable or misconfigured."
        ),
    )


@llm_router.post(
    "/discover-openhands-proxy",
    response_model=DiscoverOpenHandsProxyResponse,
)
async def discover_openhands_proxy(
    request: DiscoverOpenHandsProxyRequest,
) -> DiscoverOpenHandsProxyResponse:
    """Resolve an OpenHands account URL to its LiteLLM proxy base URL.

    Fetches ``<app_url>/api/v1/web-client/config``, reads ``llm_proxy_base_url``
    from the response, and probes the resolved proxy's liveliness endpoint.
    Never raises — every failure mode returns a structured ``error`` code so
    the frontend can render actionable copy for each fallback-matrix row.
    """
    app_url = _normalize_url(request.app_url)
    if app_url is None:
        return DiscoverOpenHandsProxyResponse(
            app_url=request.app_url,
            llm_proxy_base_url=None,
            verified=False,
            error=ERR_INVALID_URL,
            error_message=(
                f"{request.app_url!r} is not a valid http(s) URL. "
                "Expected something like https://app.all-hands.dev."
            ),
        )

    config_url = app_url + "/api/v1/web-client/config"
    async with httpx.AsyncClient(
        timeout=DISCOVERY_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        try:
            config_response = await client.get(config_url)
        except httpx.TimeoutException:
            return DiscoverOpenHandsProxyResponse(
                app_url=app_url,
                llm_proxy_base_url=None,
                verified=False,
                error=ERR_TIMEOUT,
                error_message=(
                    f"Timed out fetching {config_url}. Check the URL and your "
                    "network connection."
                ),
            )
        except httpx.HTTPError as exc:
            return DiscoverOpenHandsProxyResponse(
                app_url=app_url,
                llm_proxy_base_url=None,
                verified=False,
                error=ERR_NETWORK_ERROR,
                error_message=(
                    f"Could not reach {config_url}: {exc}. Check that the "
                    "URL is correct and reachable from this agent-server."
                ),
            )

        if config_response.status_code != 200:
            return DiscoverOpenHandsProxyResponse(
                app_url=app_url,
                llm_proxy_base_url=None,
                verified=False,
                error=ERR_NOT_OPENHANDS_INSTALL,
                error_message=(
                    f"{app_url} doesn't look like an OpenHands install: "
                    f"{config_url} returned {config_response.status_code}."
                ),
            )

        try:
            payload = config_response.json()
        except ValueError:
            return DiscoverOpenHandsProxyResponse(
                app_url=app_url,
                llm_proxy_base_url=None,
                verified=False,
                error=ERR_NOT_OPENHANDS_INSTALL,
                error_message=(
                    f"{app_url} doesn't look like an OpenHands install: "
                    f"{config_url} did not return JSON."
                ),
            )

        if not _looks_like_web_client_config(payload):
            return DiscoverOpenHandsProxyResponse(
                app_url=app_url,
                llm_proxy_base_url=None,
                verified=False,
                error=ERR_NOT_OPENHANDS_INSTALL,
                error_message=(
                    f"{app_url} doesn't look like an OpenHands install: "
                    f"{config_url} response is missing 'app_mode'."
                ),
            )

        llm_proxy_base_url = payload.get("llm_proxy_base_url")
        if not llm_proxy_base_url:
            return DiscoverOpenHandsProxyResponse(
                app_url=app_url,
                llm_proxy_base_url=None,
                verified=False,
                error=ERR_OLD_OPENHANDS_INSTALL,
                error_message=(
                    f"{app_url} is an OpenHands install, but its "
                    "WebClientConfig has no llm_proxy_base_url field. Ask "
                    "your admin to upgrade, or enter the LiteLLM proxy URL "
                    "manually."
                ),
            )

        ok, probe_error, probe_message = await _probe_liveliness(
            client, llm_proxy_base_url
        )
        # llm_proxy_base_url is returned even on probe failure so the frontend
        # can display "we resolved it to X but couldn't reach it".
        return DiscoverOpenHandsProxyResponse(
            app_url=app_url,
            llm_proxy_base_url=llm_proxy_base_url,
            verified=ok,
            error=probe_error,
            error_message=probe_message,
        )
