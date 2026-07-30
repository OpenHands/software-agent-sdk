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
    """Classify known failures from typed code and local provider metadata text."""
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
    if "429" in text or "rate limit" in text:
        return _failure(FailureKind.RATE_LIMIT, retryable=True, user_action="retry")
    if any(
        token in text
        for token in (
            "provider not provided",
            "no models loaded",
            "does not support thinking",
            "model is no longer available",
            "not found",
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
    if code in {"MaxIterationsReached", "ConversationOwnershipLostError"}:
        return _failure(FailureKind.UNKNOWN)
    if code in {
        "KeyError",
        "AssertionError",
        "PydanticSerializationError",
        "AttributeError",
        "TypeError",
    }:
        return _failure(FailureKind.INTERNAL)
    return _failure(FailureKind.UNKNOWN)


def classify_error_code(code: str) -> ErrorClassification:
    return classify_error(code)
