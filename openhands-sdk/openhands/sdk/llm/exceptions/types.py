from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SupportsMaxRetries(Protocol):
    """Protocol for exceptions and errors declaring a max_retries limit."""

    max_retries: int | None


@runtime_checkable
class SupportsRetryMetadata(SupportsMaxRetries, Protocol):
    """Protocol for exceptions and errors that declare retry metadata."""

    retry_attempt: int | None
    max_retries: int | None


class LLMError(Exception):
    message: str
    retry_attempt: int | None
    max_retries: int | None

    def __init__(
        self,
        message: str,
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_attempt = retry_attempt
        self.max_retries = max_retries

    def __str__(self) -> str:
        return self.message


# General response parsing/validation errors
class LLMMalformedActionError(LLMError):
    def __init__(
        self,
        message: str = "Malformed response",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMNoActionError(LLMError):
    def __init__(
        self,
        message: str = "Agent must return an action",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMResponseError(LLMError):
    def __init__(
        self,
        message: str = "Failed to retrieve action from LLM response",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


# Function-calling conversion/validation
class FunctionCallConversionError(LLMError):
    def __init__(
        self,
        message: str,
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class FunctionCallValidationError(LLMError):
    def __init__(
        self,
        message: str,
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class FunctionCallNotExistsError(LLMError):
    def __init__(
        self,
        message: str,
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


# Provider/transport related
class LLMNoResponseError(LLMError):
    def __init__(
        self,
        message: str = (
            "LLM did not return a response. This is only seen in Gemini models so far."
        ),
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMContextWindowExceedError(LLMError):
    def __init__(
        self,
        message: str = (
            "Conversation history longer than LLM context window limit. "
            "Consider enabling a condenser or shortening inputs."
        ),
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMMalformedConversationHistoryError(LLMError):
    def __init__(
        self,
        message: str = (
            "Conversation history produced an invalid LLM request. "
            "Consider retrying with condensed history and investigating the "
            "event stream."
        ),
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMContextWindowTooSmallError(LLMError):
    """Raised when the model's context window is too small for OpenHands to work."""

    def __init__(
        self,
        context_window: int,
        min_required: int = 16384,
        message: str | None = None,
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        if message is None:
            message = (
                f"The configured model has a context window of {context_window:,} "
                f"tokens, which is below the minimum of {min_required:,} tokens "
                "required for OpenHands to function properly.\n\n"
                "For local LLMs (Ollama, LM Studio, etc.), increase the context "
                "window.\n"
                "For cloud providers, verify you're using the correct model "
                "variant.\n\n"
                "For configuration instructions, see:\n"
                "  https://docs.openhands.dev/openhands/usage/llms/local-llms\n\n"
                "To override this check (not recommended), set the environment "
                "variable:\n"
                "  ALLOW_SHORT_CONTEXT_WINDOWS=true"
            )
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)
        self.context_window = context_window
        self.min_required = min_required


class LLMAuthenticationError(LLMError):
    def __init__(
        self,
        message: str = "Invalid or missing API credentials",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMRateLimitError(LLMError):
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMTimeoutError(LLMError):
    def __init__(
        self,
        message: str = "LLM request timed out",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMServiceUnavailableError(LLMError):
    def __init__(
        self,
        message: str = "LLM service unavailable",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMBadRequestError(LLMError):
    def __init__(
        self,
        message: str = "Bad request to LLM provider",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


class LLMContentPolicyViolationError(LLMBadRequestError):
    """Provider blocked the request/response via its content filter.

    Subclasses LLMBadRequestError for back-compat. Deterministic in
    (messages, model): a bare retry trips the same filter, so recovery
    requires changing the request, not re-sending it.
    """

    def __init__(
        self,
        message: str = "Output blocked by content filtering policy",
        retry_attempt: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        super().__init__(message, retry_attempt=retry_attempt, max_retries=max_retries)


# Other
class UserCancelledError(Exception):
    def __init__(self, message: str = "User cancelled the request") -> None:
        super().__init__(message)


class OperationCancelled(Exception):
    def __init__(self, message: str = "Operation was cancelled") -> None:
        super().__init__(message)
