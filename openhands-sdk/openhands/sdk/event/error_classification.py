"""Small, privacy-safe failure contract shared by SDK, UI, and telemetry."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FailureKind(StrEnum):
    AUTH = "auth"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    CONFIG = "config"
    TRANSIENT = "transient"
    AGENT_ACTION = "agent_action"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


FailureAction = Literal["none", "retry", "settings"]


class ErrorClassification(BaseModel):
    """The only failure metadata that crosses the event/API boundary.

    It is intentionally small. ``detail`` is inspected locally only to map
    broad third-party errors to this closed vocabulary; it is never copied
    here or sent to telemetry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FailureKind
    retryable: bool
    user_action: FailureAction = "none"
    error_id: str | None = None


def _failure(
    kind: FailureKind, *, retryable: bool = False, user_action: FailureAction = "none"
) -> ErrorClassification:
    return ErrorClassification(kind=kind, retryable=retryable, user_action=user_action)


def classify_error(code: str, detail: str = "") -> ErrorClassification:
    """Classify known failures from typed code and local provider metadata text.

    Exception classes whose name alone is authoritative (``KeyError``,
    ``AssertionError``, SDK-specific errors like ``LLMAuthenticationError``,
    ``MaxIterationsReached``, …) are checked **first**, so incidental wording
    in ``detail`` cannot override them. Opaque/generic wrapper codes
    (``OpenAIError``, ``APIError``, ``HTTPStatusError``, …) are checked
    **after** the detail heuristics, because the class name alone is not
    specific enough — the detail text is needed to distinguish auth from
    rate-limit from transient.
    """
    # ── authoritative code-based classification (checked first) ──────────
    if code in {"LLMAuthenticationError", "ACPAuthRequired"}:
        return _failure(FailureKind.AUTH, user_action="settings")
    if code in {"LLMRateLimitError"}:
        return _failure(FailureKind.RATE_LIMIT, retryable=True, user_action="retry")
    if code in {
        "LLMBadRequestError",
        "ACPInitError",
        "ACPSpawnError",
        "ACPPromptError",
        "NotFoundError",
        "MaxBudgetReached",
        "LibTmuxException",
    }:
        return _failure(FailureKind.CONFIG, user_action="settings")
    # Context-window / conversation-history errors are recoverable via
    # condensation, so classify them as agent outcomes, not diagnostics.
    if code in {"LLMContextWindowExceedError", "LLMMalformedConversationHistoryError"}:
        return _failure(FailureKind.AGENT_ACTION, retryable=True, user_action="retry")
    # Run-limit and ownership-loss are known product outcomes, not diagnostics.
    if code in {"MaxIterationsReached", "ConversationOwnershipLostError"}:
        return _failure(FailureKind.AGENT_ACTION)
    if code in {
        "KeyError",
        "AssertionError",
        "PydanticSerializationError",
        "AttributeError",
        "TypeError",
    }:
        return _failure(FailureKind.INTERNAL)

    # ── detail-based classification (for opaque/generic wrapper codes) ───
    text = detail.casefold()

    if any(
        token in text
        for token in (
            "invalid api key",
            "incorrect api key",
            "authentication required",
            "invalid bearer token",
            "invalid proxy server token",
            "unauthorized",
            "error code: 401",
            'status": 401',
            "token_not_found",
            "api key is missing",
        )
    ):
        return _failure(FailureKind.AUTH, user_action="settings")
    if any(
        token in text
        for token in (
            "weekly usage limit",
            "daily quota",
            "session usage limit",
            "insufficient balance",
            "more credits",
            "budget has been exceeded",
        )
    ):
        return _failure(FailureKind.QUOTA, user_action="settings")
    if "rate limit" in text or "error code: 429" in text or 'status": 429' in text:
        return _failure(FailureKind.RATE_LIMIT, retryable=True, user_action="retry")
    if any(
        token in text
        for token in (
            "provider not provided",
            "no models loaded",
            "does not support thinking",
            "model is no longer available",
            "model not found",
            "invalid params",
            "inactive_service",
            "powershell is not available",
        )
    ):
        return _failure(FailureKind.CONFIG, user_action="settings")
    if any(
        token in text
        for token in (
            "timeout",
            "connection error",
            "connection closed",
            "service temporarily unavailable",
            "bad gateway",
            "cloudflare",
            "cannot connect",
            "name or service not known",
            "error code: 5",
        )
    ):
        return _failure(FailureKind.TRANSIENT, retryable=True, user_action="retry")
    if any(
        token in text
        for token in (
            "on_token callback",
            "duplicate tool names",
            "list_tools",
            "on_tools_changed",
            "surrogates not allowed",
        )
    ):
        return _failure(FailureKind.INTERNAL)

    # ── fallback code-based classification (generic wrapper codes) ───────
    if code in {
        "LLMServiceUnavailableError",
        "LLMTimeoutError",
        "ReadTimeout",
        "LLMNoResponseError",
        "MCPTimeoutError",
        "BadGatewayError",
        "HTTPStatusError",
        "RequestError",
        "CloudflareError",
        "OpenAIError",
        "APIError",
        "BaseLLMException",
        "AnthropicError",
        "OpenRouterException",
        "OllamaError",
    }:
        return _failure(FailureKind.TRANSIENT, retryable=True, user_action="retry")

    return _failure(FailureKind.UNKNOWN)
