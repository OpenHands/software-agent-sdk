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
    is_context_window_exceeded,
    looks_like_auth_error,
    looks_like_malformed_conversation_history_error,
)
from .types import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMContextWindowExceedError,
    LLMMalformedConversationHistoryError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


def map_provider_exception(exception: Exception) -> Exception:
    """
    Map provider/LiteLLM exceptions to SDK-typed exceptions.

    Returns original exception if no mapping applies.
    """
    # Context window exceeded first (highest priority among normal retries)
    if is_context_window_exceeded(exception):
        return LLMContextWindowExceedError(str(exception))

    # Malformed prompt history is distinct from context-window exhaustion even
    # though the recovery path still uses condensation.
    if looks_like_malformed_conversation_history_error(exception):
        return LLMMalformedConversationHistoryError(str(exception))

    # Auth-like errors often appear as BadRequest/OpenAIError with specific text
    if looks_like_auth_error(exception):
        return LLMAuthenticationError(str(exception))

    if isinstance(exception, RateLimitError):
        return LLMRateLimitError(str(exception))

    if isinstance(exception, LiteLLMTimeout):
        return LLMTimeoutError(str(exception))

    # Connectivity and service-side availability issues → service unavailable
    if isinstance(
        exception, (APIConnectionError, ServiceUnavailableError, InternalServerError)
    ):
        return LLMServiceUnavailableError(str(exception))

    # Generic client-side 4xx errors
    if isinstance(exception, BadRequestError):
        return LLMBadRequestError(str(exception))

    # Unknown: let caller re-raise original
    return exception
