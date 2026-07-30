"""Structured, safe error semantics shared by conversation event consumers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


ErrorOrigin = Literal[
    "sdk", "provider", "agent", "tool", "mcp", "environment", "unknown"
]
ErrorCause = Literal[
    "authentication",
    "configuration",
    "quota",
    "rate_limit",
    "provider_unavailable",
    "network",
    "tool_input",
    "run_limit",
    "cancelled",
    "internal",
]
ErrorBlame = Literal[
    "user_configuration", "external", "agent_behavior", "product_defect", "unknown"
]
ErrorImpact = Literal["notice", "step_failed", "run_stopped", "conversation_unusable"]
ErrorRetry = Literal["none", "immediate", "after_backoff", "after_user_action"]
ErrorAction = Literal[
    "none",
    "retry",
    "reauthenticate",
    "configure_llm",
    "select_model",
    "contact_support",
]
ErrorPresentation = Literal["info", "warning", "error"]
ErrorTelemetry = Literal["none", "outcome", "diagnostic"]


class ErrorClassification(BaseModel):
    """Closed-vocabulary metadata for UI and telemetry; never contains error text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: ErrorOrigin
    cause: ErrorCause
    blame: ErrorBlame
    impact: ErrorImpact
    retry: ErrorRetry = "none"
    user_action: ErrorAction = "none"
    presentation: ErrorPresentation
    telemetry: ErrorTelemetry


def _outcome(
    *,
    cause: ErrorCause,
    blame: ErrorBlame,
    retry: ErrorRetry = "none",
    user_action: ErrorAction = "none",
    origin: ErrorOrigin = "provider",
) -> ErrorClassification:
    return ErrorClassification(
        origin=origin,
        cause=cause,
        blame=blame,
        impact="run_stopped",
        retry=retry,
        user_action=user_action,
        presentation="warning",
        telemetry="outcome",
    )


def _diagnostic() -> ErrorClassification:
    return ErrorClassification(
        origin="sdk",
        cause="internal",
        blame="product_defect",
        impact="run_stopped",
        user_action="contact_support",
        presentation="error",
        telemetry="diagnostic",
    )


def classify_error(code: str, detail: str = "") -> ErrorClassification:
    """Classify an error locally, reducing details to a closed vocabulary.

    ``detail`` is only inspected in memory to distinguish provider responses
    sharing a broad exception type. It is never stored in the classification or
    telemetry payload.
    """
    normalized = detail.casefold()
    if any(
        token in normalized
        for token in (
            "invalid api key",
            "incorrect api key",
            "authentication required",
            "invalid bearer token",
            "token_not_found",
            "unauthorized",
            "api key is missing",
        )
    ):
        return _outcome(
            cause="authentication",
            blame="user_configuration",
            retry="after_user_action",
            user_action="reauthenticate",
        )
    if any(
        token in normalized
        for token in (
            "weekly usage limit",
            "daily quota",
            "session usage limit",
            "insufficient balance",
            "more credits",
            "budget has been exceeded",
            "exhausted your",
        )
    ):
        return _outcome(
            cause="quota",
            blame="user_configuration",
            retry="after_user_action",
            user_action="configure_llm",
        )
    if "429" in normalized or "rate limit" in normalized:
        return _outcome(
            cause="rate_limit",
            blame="external",
            retry="after_backoff",
            user_action="retry",
        )
    if any(
        token in normalized
        for token in (
            "provider not provided",
            "no models loaded",
            "does not support thinking",
            "model is no longer available",
            "404 page not found",
            "not found",
            "invalid params",
            "inactive_service",
        )
    ):
        return _outcome(
            cause="configuration",
            blame="user_configuration",
            retry="after_user_action",
            user_action="configure_llm",
        )
    if any(
        token in normalized
        for token in (
            "timeout",
            "connection error",
            "connection closed",
            "service temporarily unavailable",
            "bad gateway",
            "cloudflare",
            "cannot connect",
            "name or service not known",
            "503",
            "502",
        )
    ):
        return _outcome(
            cause="provider_unavailable",
            blame="external",
            retry="after_backoff",
            user_action="retry",
        )
    if any(
        token in normalized
        for token in (
            "streaming requires an on_token callback",
            "duplicate tool names",
            "has no attribute 'list_tools'",
            "unexpected keyword argument 'on_tools_changed'",
            "surrogates not allowed",
        )
    ):
        return _diagnostic()
    if (
        "powershell is not available" in normalized
        or "error creating" in normalized
        and "tmux" in normalized
    ):
        return _outcome(
            cause="configuration",
            blame="user_configuration",
            retry="after_user_action",
            user_action="configure_llm",
            origin="environment",
        )

    if code in {"LLMAuthenticationError", "ACPAuthRequired"}:
        return _outcome(
            cause="authentication",
            blame="user_configuration",
            retry="after_user_action",
            user_action="reauthenticate",
        )
    if code in {
        "LLMBadRequestError",
        "ACPInitError",
        "ACPSpawnError",
        "ACPPromptError",
        "NotFoundError",
    }:
        return _outcome(
            cause="configuration",
            blame="user_configuration",
            retry="after_user_action",
            user_action="configure_llm",
        )
    if code in {"LLMRateLimitError", "MaxBudgetReached"}:
        return _outcome(
            cause="rate_limit" if code == "LLMRateLimitError" else "quota",
            blame="external" if code == "LLMRateLimitError" else "user_configuration",
            retry="after_backoff",
            user_action="retry",
        )
    if code in {
        "LLMServiceUnavailableError",
        "LLMTimeoutError",
        "ReadTimeout",
        "LLMNoResponseError",
        "OpenAIError",
        "APIError",
        "BaseLLMException",
        "AnthropicError",
        "OpenRouterException",
        "OllamaError",
        "BadGatewayError",
        "HTTPStatusError",
        "RequestError",
        "CloudflareError",
    }:
        return _outcome(
            cause="provider_unavailable",
            blame="external",
            retry="after_backoff",
            user_action="retry",
        )
    if code in {"MaxIterationsReached", "ConversationOwnershipLostError"}:
        return _outcome(cause="run_limit", blame="unknown", origin="sdk")
    return _diagnostic()


def classify_error_code(code: str) -> ErrorClassification:
    """Compatibility wrapper for callers without detail text."""
    return classify_error(code)
