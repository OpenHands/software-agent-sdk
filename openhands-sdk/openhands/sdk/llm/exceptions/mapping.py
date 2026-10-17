from __future__ import annotations

from httpx import HTTPStatusError
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LiteLLMTimeout,
)
from openai import APIStatusError, OpenAIError

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
    LLMNotFoundError,
    LLMProviderError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


def _map_http_status(status: int) -> LLMError:
    if status in (401, 403):
        return LLMAuthenticationError(
            "Access to the model was denied. "
            "Check your credentials and model permissions."
        )
    if status == 404:
        return LLMNotFoundError()
    if status == 429:
        return LLMRateLimitError("The provider rate limit was reached. Retry later.")
    if status in (408, 504):
        return LLMTimeoutError("The provider request timed out. Retry later.")
    if status == 409 or 500 <= status < 600:
        return LLMServiceUnavailableError(
            "The model provider is temporarily unavailable. Retry later."
        )
    if 400 <= status < 500:
        return LLMBadRequestError(
            "The provider rejected the request. Check your model and request settings."
        )
    return LLMProviderError()


def map_provider_exception(exception: Exception) -> Exception:
    """
    Map provider/LiteLLM exceptions to SDK-typed exceptions.

    Returns original exception for errors outside the provider/transport boundary.
    """
    # Preserve the specialized recovery of already-supported provider errors.
    if not isinstance(
        exception,
        (
            APIConnectionError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            PermissionDeniedError,
            RateLimitError,
            ServiceUnavailableError,
            LiteLLMTimeout,
        ),
    ):
        if isinstance(exception, (APIError, APIStatusError)):
            return _map_http_status(exception.status_code)
        if isinstance(exception, HTTPStatusError):
            return _map_http_status(exception.response.status_code)

    # Context window exceeded first (highest priority among normal retries)
    if is_context_window_exceeded(exception):
        return LLMContextWindowExceedError(str(exception))

    # Malformed prompt history is distinct from context-window exhaustion even
    # though the recovery path still uses condensation.
    if looks_like_malformed_conversation_history_error(exception):
        return LLMMalformedConversationHistoryError(str(exception))

    # Auth-like errors often appear as BadRequest/OpenAIError with specific text
    if looks_like_auth_error(exception):
        if isinstance(exception, PermissionDeniedError):
            return _map_http_status(403)
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

    # Content-policy blocks are deterministic 4xx; distinguish them from generic
    # bad requests so the agent can recover softly instead of hard-erroring.
    if is_content_policy_violation(exception):
        return LLMContentPolicyViolationError(str(exception))

    # Generic client-side 4xx errors
    if isinstance(exception, BadRequestError):
        return LLMBadRequestError(str(exception))

    if isinstance(exception, OpenAIError):
        return LLMProviderError()

    return exception
