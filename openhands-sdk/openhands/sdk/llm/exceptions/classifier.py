from __future__ import annotations

from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
    OpenAIError,
    PermissionDeniedError,
)

from .types import (
    LLMContextWindowExceedError,
    LLMMalformedConversationHistoryError,
)


# Minimal, provider-agnostic context-window detection
LONG_PROMPT_PATTERNS: list[str] = [
    "contextwindowexceedederror",
    "prompt is too long",
    "input length and `max_tokens` exceed context limit",
    "please reduce the length of",
    "the request exceeds the available context size",
    "context length exceeded",
    "input exceeds the context window",
    "context window exceeds limit",  # Minimax provider
]

# These indicate malformed tool-use/tool-result history being sent to the
# provider. They are tracked separately from true context-window errors so the
# logs and agent control flow can preserve that distinction while still routing
# into condensation-based recovery.
MALFORMED_HISTORY_PATTERNS: list[str] = [
    "tool_use ids were found without `tool_result` blocks immediately after",
    (
        "each `tool_use` block must have a corresponding `tool_result` block "
        "in the next message"
    ),
    "each tool_use must have a single result",
    "found multiple `tool_result` blocks with id:",
    "unexpected `tool_use_id` found in `tool_result` blocks",
    (
        "each `tool_result` block must have a corresponding `tool_use` block "
        "in the previous message"
    ),
]


def is_context_window_exceeded(exception: Exception) -> bool:
    if isinstance(exception, (ContextWindowExceededError, LLMContextWindowExceedError)):
        return True

    # Check for litellm/openai exception types that may contain context window errors.
    # APIConnectionError can wrap provider-specific errors (e.g., Minimax) that include
    # context window messages in their error text.
    if not isinstance(exception, (BadRequestError, OpenAIError, APIConnectionError)):
        return False

    s = str(exception).lower()
    return any(p in s for p in LONG_PROMPT_PATTERNS)


def looks_like_malformed_conversation_history_error(exception: Exception) -> bool:
    if isinstance(exception, LLMMalformedConversationHistoryError):
        return True

    if not isinstance(exception, (BadRequestError, OpenAIError, APIConnectionError)):
        return False

    s = str(exception).lower()
    return any(p in s for p in MALFORMED_HISTORY_PATTERNS)


# Vertex AI (Gemini) rejects context-caching requests when the cached content
# is below the provider's minimum token threshold (currently 4096 tokens).
# Example error: "The cached content is of 1171 tokens. The minimum token
# count to start caching is 4096." — the `.lower()` comparison handles case
# variation across providers but won't match reworded messages; update this
# pattern if the API phrasing changes.
PROMPT_CACHE_TOO_SMALL_PATTERNS: list[str] = [
    "minimum token count to start caching",
]

AUTH_PATTERNS: list[str] = [
    "invalid api key",
    "unauthorized",
    "missing api key",
    "invalid authentication",
    "access denied",
]


def is_prompt_cache_too_small(exception: Exception) -> bool:
    """Return True if the error indicates the prompt cache content is too small.

    Vertex AI (Gemini) requires a minimum number of tokens (currently 4096)
    to create a context cache. When the cached content is below this threshold,
    the API returns a 400 error. The SDK should detect this and retry without
    prompt caching markers.
    """
    if not isinstance(exception, (BadRequestError, OpenAIError)):
        return False
    s = str(exception).lower()
    return any(p in s for p in PROMPT_CACHE_TOO_SMALL_PATTERNS)


def looks_like_auth_error(exception: Exception) -> bool:
    # Trust the typed exception when the provider/LiteLLM raised an explicit
    # 401/403 — its message text may not contain the heuristic patterns below.
    if isinstance(exception, (AuthenticationError, PermissionDeniedError)):
        return True
    if not isinstance(exception, (BadRequestError, OpenAIError)):
        return False
    s = str(exception).lower()
    if any(p in s for p in AUTH_PATTERNS):
        return True
    # Some providers include explicit status codes in message text
    for code in ("status 401", "status 403"):
        if code in s:
            return True
    return False
