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


def classify_error_code(code: str) -> ErrorClassification:
    """Classify stable event codes without inspecting potentially sensitive details."""
    if code in {"LLMAuthenticationError", "ACPAuthRequired"}:
        return ErrorClassification(
            origin="provider",
            cause="authentication",
            blame="user_configuration",
            impact="run_stopped",
            retry="after_user_action",
            user_action="reauthenticate",
            presentation="warning",
            telemetry="outcome",
        )
    if code in {"LLMBadRequestError", "ACPInitError", "ACPSpawnError", "NotFoundError"}:
        return ErrorClassification(
            origin="provider",
            cause="configuration",
            blame="user_configuration",
            impact="run_stopped",
            retry="after_user_action",
            user_action="configure_llm",
            presentation="warning",
            telemetry="outcome",
        )
    if code in {"LLMRateLimitError", "MaxBudgetReached"}:
        return ErrorClassification(
            origin="provider",
            cause="rate_limit" if code == "LLMRateLimitError" else "quota",
            blame="external" if code == "LLMRateLimitError" else "user_configuration",
            impact="run_stopped",
            retry="after_backoff",
            user_action="retry",
            presentation="warning",
            telemetry="outcome",
        )
    if code in {
        "LLMServiceUnavailableError",
        "LLMTimeoutError",
        "ReadTimeout",
        "LLMNoResponseError",
    }:
        return ErrorClassification(
            origin="provider",
            cause="provider_unavailable",
            blame="external",
            impact="run_stopped",
            retry="after_backoff",
            user_action="retry",
            presentation="warning",
            telemetry="outcome",
        )
    if code in {"MaxIterationsReached", "ConversationOwnershipLostError"}:
        return ErrorClassification(
            origin="sdk",
            cause="run_limit",
            blame="unknown",
            impact="run_stopped",
            presentation="warning",
            telemetry="outcome",
        )
    return ErrorClassification(
        origin="sdk",
        cause="internal",
        blame="product_defect",
        impact="run_stopped",
        user_action="contact_support",
        presentation="error",
        telemetry="diagnostic",
    )
