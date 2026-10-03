from __future__ import annotations

from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LiteLLMTimeout,
)

from .classifier import (
    is_content_policy_violation,
    is_context_window_exceeded,
    looks_like_auth_error,
    looks_like_malformed_conversation_history_error,
)
from .types import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMContentPolicyViolationError,
    LLMContextWindowExceedError,
    LLMError,
    LLMMalformedConversationHistoryError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
    SupportsMaxRetries,
)


def map_provider_exception(exception: Exception) -> Exception:
    """
    Map provider/LiteLLM exceptions to SDK-typed exceptions.

    Returns original exception if no mapping applies.
    """
    mapped: Exception
    # Context window exceeded first (highest priority among normal retries)
    if is_context_window_exceeded(exception):
        mapped = LLMContextWindowExceedError(str(exception))
    # Malformed prompt history is distinct from context-window exhaustion even
    # though the recovery path still uses condensation.
    elif looks_like_malformed_conversation_history_error(exception):
        mapped = LLMMalformedConversationHistoryError(str(exception))
    # Auth-like errors often appear as BadRequest/OpenAIError with specific text
    elif looks_like_auth_error(exception):
        mapped = LLMAuthenticationError(str(exception))
    elif isinstance(exception, RateLimitError):
        mapped = LLMRateLimitError(str(exception))
    elif isinstance(exception, LiteLLMTimeout):
        mapped = LLMTimeoutError(str(exception))
    # Connectivity and service-side availability issues → service unavailable
    elif isinstance(
        exception, (APIConnectionError, ServiceUnavailableError, InternalServerError)
    ):
        mapped = LLMServiceUnavailableError(str(exception))
    # Content-policy blocks are deterministic 4xx; distinguish them from generic
    # bad requests so the agent can recover softly instead of hard-erroring.
    elif is_content_policy_violation(exception):
        mapped = LLMContentPolicyViolationError(str(exception))
    # Generic client-side 4xx errors
    elif isinstance(exception, BadRequestError):
        mapped = LLMBadRequestError(str(exception))
    else:
        # Unknown: let caller re-raise original
        return exception

    if isinstance(mapped, LLMError):
        if mapped.retry_attempt is None and hasattr(exception, "retry_attempt"):
            mapped.retry_attempt = exception.retry_attempt  # type: ignore[attr-defined]
        if mapped.max_retries is None and isinstance(exception, SupportsMaxRetries):
            mapped.max_retries = exception.max_retries

    return mapped
