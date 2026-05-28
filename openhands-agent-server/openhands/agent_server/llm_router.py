"""Router for LLM model and provider information endpoints."""

import asyncio
from enum import Enum

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from openhands.sdk.llm.llm import LLM_SECRET_FIELDS
from openhands.sdk.llm.utils.litellm_provider import infer_litellm_provider
from openhands.sdk.llm.utils.unverified_models import (
    _extract_model_and_provider,
    _get_litellm_provider_names,
    get_supported_llm_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

llm_router = APIRouter(prefix="/llm", tags=["LLM"])

# Hard ceiling for the verify probe so an unresponsive provider can't park a
# UI "Verify credentials" button on a 5-minute hang (the SDK's
# ``LLM.timeout`` default). Callers can shorten this by setting
# ``timeout`` to a smaller value in the request body; we take the
# ``min`` so they can never extend it past what is a reasonable
# interactive wait.
_VERIFY_TIMEOUT_S = 30.0

# Maximum number of characters of provider-side error text we forward to the
# client. Caps any accidental large payload (truncated HTML error pages,
# verbose stack traces, etc.) and bounds the worst case if a provider were
# ever to echo request content back in an error body.
_MAX_ERROR_MESSAGE_CHARS = 512


class ProvidersResponse(BaseModel):
    """Response containing the list of available LLM providers."""

    providers: list[str]


class ModelsResponse(BaseModel):
    """Response containing the list of available LLM models."""

    models: list[str]


class VerifiedModelsResponse(BaseModel):
    """Response containing verified models organized by provider."""

    models: dict[str, list[str]]


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


# ─────────────────────────────────────────────────────────────────────────────
# Verify endpoint
# ─────────────────────────────────────────────────────────────────────────────


class VerifyLLMStatus(str, Enum):
    """Outcome categories surfaced by ``POST /llm/verify``.

    All non-SUCCESS values are returned with HTTP 200 — clients should branch
    on ``status``, not on transport errors. ``RATE_LIMITED`` is reported
    separately from ``SUCCESS`` so the UI can show a soft-success banner, but
    callers may treat both as "credentials are valid".
    """

    SUCCESS = "success"
    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    BAD_REQUEST = "bad_request"
    UNKNOWN_ERROR = "unknown_error"


class VerifyLLMResponse(BaseModel):
    """Result of a verify probe.

    A successful probe returns ``status=SUCCESS`` and the inferred LiteLLM
    provider name. All failure modes are reported with HTTP 200 and a
    discriminated ``status`` so clients have a single decision tree.
    """

    status: VerifyLLMStatus
    message: str | None = Field(
        default=None,
        description="Human-readable detail from the provider, if available.",
    )
    provider: str | None = Field(
        default=None,
        description="LiteLLM provider name inferred from model + base_url.",
    )


def _sanitize_error_message(exc: Exception, llm: LLM) -> str:
    """Render a provider exception as a client-safe message string.

    Defends against two failure modes:

    1. **Credential leakage**: some providers echo fragments of the request
       (including ``Authorization`` headers or query-string keys) back in
       their error bodies. LiteLLM normally surfaces these as the
       exception's ``str()``. We scrub any of the ``SecretStr`` values that
       appear on the ``LLM`` instance so a leaked echo collapses to
       ``***`` before crossing the API boundary.
    2. **Pathological size**: an HTML error page or large JSON blob in
       ``str(exc)`` would otherwise flow straight into the JSON response.
       We truncate to ``_MAX_ERROR_MESSAGE_CHARS``.
    """
    message = str(exc)
    for field_name in LLM_SECRET_FIELDS:
        value = getattr(llm, field_name, None)
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            # Only redact non-trivial values — replacing the empty string
            # would corrupt every character boundary in the message.
            if raw:
                message = message.replace(raw, "***")
    if len(message) > _MAX_ERROR_MESSAGE_CHARS:
        message = message[: _MAX_ERROR_MESSAGE_CHARS - 1] + "…"
    return message


def _verify_response_for_exception(exc: Exception, llm: LLM) -> VerifyLLMResponse:
    """Map a verify-time exception to the appropriate response.

    Handled error classes correspond to the typed exceptions raised by
    :meth:`LLM.verify`, plus ``asyncio.TimeoutError`` from the wait_for cap
    enforced by the verify endpoint. Anything else collapses to
    ``UNKNOWN_ERROR`` so the endpoint never raises and the frontend always
    has a structured result to branch on.
    """
    message = _sanitize_error_message(exc, llm)
    if isinstance(exc, LLMAuthenticationError):
        return VerifyLLMResponse(status=VerifyLLMStatus.AUTH_ERROR, message=message)
    if isinstance(exc, LLMRateLimitError):
        return VerifyLLMResponse(status=VerifyLLMStatus.RATE_LIMITED, message=message)
    if isinstance(exc, (LLMTimeoutError, asyncio.TimeoutError)):
        return VerifyLLMResponse(status=VerifyLLMStatus.TIMEOUT, message=message)
    if isinstance(exc, LLMServiceUnavailableError):
        return VerifyLLMResponse(status=VerifyLLMStatus.UNREACHABLE, message=message)
    if isinstance(exc, LLMBadRequestError):
        return VerifyLLMResponse(status=VerifyLLMStatus.BAD_REQUEST, message=message)
    logger.error("llm.verify failed with an unmapped exception: %s", message)
    return VerifyLLMResponse(status=VerifyLLMStatus.UNKNOWN_ERROR, message=message)


@llm_router.post("/verify", response_model=VerifyLLMResponse)
async def verify_llm_config(llm: LLM) -> VerifyLLMResponse:
    """Verify that the provided LLM credentials can reach the provider.

    Accepts an :class:`LLM` config in the request body and sends a single
    one-token probe through :meth:`LLM.averify`, reporting the outcome as a
    structured ``VerifyLLMResponse``. The probe always completes with HTTP
    200; failure modes are encoded in ``status``. Malformed bodies (e.g.
    missing ``model``) surface as the usual FastAPI 422.

    Verifying from the agent server (rather than the browser) means:

    - Every LiteLLM-supported provider is reachable, including Bedrock with
      SigV4 / IAM and Azure with ``api_version``.
    - No CORS restrictions, no provider-specific request shape to maintain
      on the client.
    - The verify call path is the same code path used at conversation time,
      so "verified" really does mean "the agent will be able to use this".

    The probe is hard-capped at :data:`_VERIFY_TIMEOUT_S` seconds (taking
    the ``min`` with any smaller ``timeout`` the caller passed in the body)
    so an unresponsive provider can't park an interactive UI on the SDK's
    300 s default. A timeout is reported as ``status=TIMEOUT``.
    """
    provider = None
    timeout = min(
        llm.timeout if llm.timeout is not None else _VERIFY_TIMEOUT_S,
        _VERIFY_TIMEOUT_S,
    )
    try:
        provider = infer_litellm_provider(model=llm.model, api_base=llm.base_url)
        await asyncio.wait_for(llm.averify(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — verify must never raise
        result = _verify_response_for_exception(exc, llm)
        if result.provider is None:
            result = result.model_copy(update={"provider": provider})
        return result
    return VerifyLLMResponse(status=VerifyLLMStatus.SUCCESS, provider=provider)
