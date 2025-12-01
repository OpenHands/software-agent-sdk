from dataclasses import dataclass


def model_matches(model: str, patterns: list[str]) -> bool:
    """Return True if any pattern appears as a substring in the raw model name.

    Matching semantics:
    - Case-insensitive substring search on full raw model string
    """
    raw = (model or "").strip().lower()
    for pat in patterns:
        token = pat.strip().lower()
        if token in raw:
            return True
    return False


@dataclass(frozen=True)
class ModelFeatures:
    supports_reasoning_effort: bool
    supports_extended_thinking: bool
    supports_prompt_cache: bool
    supports_stop_words: bool
    supports_responses_api: bool
    force_string_serializer: bool
    send_reasoning_content: bool
    supports_prompt_cache_retention: bool


# Pattern tables capturing current behavior. Keep patterns lowercase.

REASONING_EFFORT_PATTERNS: list[str] = [
    # Mirror main behavior exactly (no unintended expansion)
    "o1-2024-12-17",
    "o1",
    "o3",
    "o3-2025-04-16",
    "o3-mini-2025-01-31",
    "o3-mini",
    "o4-mini",
    "o4-mini-2025-04-16",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    # OpenAI GPT-5 family (includes mini variants)
    "gpt-5",
    # Anthropic Opus 4.5
    "claude-opus-4-5",
]

EXTENDED_THINKING_PATTERNS: list[str] = [
    # Anthropic model family
    # We did not include sonnet 3.7 and 4 here as they don't brings
    # significant performance improvements for agents
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
]

PROMPT_CACHE_PATTERNS: list[str] = [
    "claude-3-7-sonnet",
    "claude-sonnet-3-7-latest",
    "claude-3-5-sonnet",
    "claude-3-5-haiku",
    "claude-3-haiku-20240307",
    "claude-3-opus-20240229",
    "claude-sonnet-4",
    "claude-opus-4",
    # Anthropic Haiku 4.5 variants (dash only; official IDs use hyphens)
    "claude-haiku-4-5",
    "claude-opus-4-5",
]

# Models that support a top-level prompt_cache_retention parameter
PROMPT_CACHE_RETENTION_PATTERNS: list[str] = [
    # OpenAI GPT-5+ family
    "gpt-5",
    # GPT-4.1 too
    "gpt-4.1",
]

SUPPORTS_STOP_WORDS_FALSE_PATTERNS: list[str] = [
    # o-series families don't support stop words
    "o1",
    "o3",
    # grok-4 specific model name (basename)
    "grok-4-0709",
    "grok-code-fast-1",
    # DeepSeek R1 family
    "deepseek-r1-0528",
]

# Models that should use the OpenAI Responses API path by default
RESPONSES_API_PATTERNS: list[str] = [
    # OpenAI GPT-5 family (includes mini variants)
    "gpt-5",
    # OpenAI Codex (uses Responses API)
    "codex-mini-latest",
]

# Models that require string serializer for tool messages
# These models don't support structured content format [{"type":"text","text":"..."}]
# and need plain strings instead
# NOTE: model_matches uses case-insensitive substring matching, not globbing.
#       Keep these entries as bare substrings without wildcards.
FORCE_STRING_SERIALIZER_PATTERNS: list[str] = [
    "deepseek",  # e.g., DeepSeek-V3.2-Exp
    "glm",  # e.g., GLM-4.5 / GLM-4.6
    # Kimi K2-Instruct requires string serialization only on Groq
    "groq/kimi-k2-instruct",  # explicit provider-prefixed IDs
]

# Models that we should send full reasoning content
# in the message input
SEND_REASONING_CONTENT_PATTERNS: list[str] = [
    "kimi-k2-thinking",
]


def get_features(model: str) -> ModelFeatures:
    """Get model features."""
    return ModelFeatures(
        supports_reasoning_effort=model_matches(model, REASONING_EFFORT_PATTERNS),
        supports_extended_thinking=model_matches(model, EXTENDED_THINKING_PATTERNS),
        supports_prompt_cache=model_matches(model, PROMPT_CACHE_PATTERNS),
        supports_stop_words=not model_matches(
            model, SUPPORTS_STOP_WORDS_FALSE_PATTERNS
        ),
        supports_responses_api=model_matches(model, RESPONSES_API_PATTERNS),
        force_string_serializer=model_matches(model, FORCE_STRING_SERIALIZER_PATTERNS),
        send_reasoning_content=model_matches(model, SEND_REASONING_CONTENT_PATTERNS),
        supports_prompt_cache_retention=model_matches(
            model, PROMPT_CACHE_RETENTION_PATTERNS
        ),
    )


# Default temperature mapping.
# Each entry: (pattern, default_temperature)
DEFAULT_TEMPERATURE_PATTERNS: list[tuple[str, float]] = [
    ("kimi-k2-thinking", 1.0),
]


def get_default_temperature(model: str) -> float:
    """Return the default temperature for a given model pattern.

    Uses case-insensitive substring matching via model_matches.
    """
    for pattern, value in DEFAULT_TEMPERATURE_PATTERNS:
        if model_matches(model, [pattern]):
            return value
    return 0.0
