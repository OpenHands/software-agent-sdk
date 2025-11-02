from __future__ import annotations

from litellm.exceptions import (
    BadRequestError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LiteLLMTimeout,
)

from .classifier import is_context_window_exceeded, looks_like_auth_error
from .types import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMContextWindowExceedError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


def map_provider_exception(exception: Exception) -> Exception:
    """
    Map provider/LiteLLM exceptions to SDK-typed exceptions.

    Returns original exception if no mapping applies.
    """
    # Context window exceeded first (highest priority)
    if is_context_window_exceeded(exception):
        return LLMContextWindowExceedError(str(exception))

    # Auth-like errors often appear as BadRequest/OpenAIError with specific text
    if looks_like_auth_error(exception):
        return LLMAuthenticationError(str(exception))

    if isinstance(exception, RateLimitError):
        return LLMRateLimitError(str(exception))

    if isinstance(exception, LiteLLMTimeout):
        return LLMTimeoutError(str(exception))

    # Treat true service-side availability issues as service unavailable
    # but keep APIConnectionError as-is for backward compatibility / caller expectations
    if isinstance(exception, (ServiceUnavailableError, InternalServerError)):
        return LLMServiceUnavailableError(str(exception))

    # Generic client-side 4xx errors
    if isinstance(exception, BadRequestError):
        return LLMBadRequestError(str(exception))

    # Unknown: let caller re-raise original
    return exception
