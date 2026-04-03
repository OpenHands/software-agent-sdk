from dataclasses import dataclass
from functools import cache

from litellm import get_supported_openai_params


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


def apply_ordered_model_rules(model: str, rules: list[str]) -> bool:
    """Apply ordered include/exclude model rules to determine final support.

    Rules semantics:
    - Each entry is a substring token. '!' prefix marks an exclude rule.
    - Case-insensitive substring matching against the raw model string.
    - Evaluated in order; the last matching rule wins.
    - If no rule matches, returns False.
    """
    raw = (model or "").strip().lower()
    decided: bool | None = None
    for rule in rules:
        token = rule.strip().lower()
        if not token:
            continue
        is_exclude = token.startswith("!")
        core = token[1:] if is_exclude else token
        if core and core in raw:
            decided = not is_exclude
    return bool(decided)


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


LITELLM_PROXY_PREFIX = "litellm_proxy/"

# Common deployment path prefixes used in LiteLLM proxy configurations
DEPLOYMENT_PREFIXES = ("prod/", "dev/", "staging/", "test/")


@cache
def _normalized_supported_openai_params(model: str | None) -> frozenset[str]:
    """Return LiteLLM-supported OpenAI params for a normalized model name."""
    if not model:
        return frozenset()

    normalized = model.strip().lower()
    if normalized.startswith(LITELLM_PROXY_PREFIX):
        normalized = normalized.removeprefix(LITELLM_PROXY_PREFIX)

    # Strip common deployment path prefixes (e.g., "prod/claude-opus-4-5" → "claude-opus-4-5")
    for prefix in DEPLOYMENT_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break

    params = get_supported_openai_params(
        model=normalized,
        custom_llm_provider=None,
    )
    return frozenset(params or ())


def _supports_reasoning_effort(model: str | None) -> bool:
    """Return True if LiteLLM says the model accepts reasoning_effort."""
    return "reasoning_effort" in _normalized_supported_openai_params(model)


EXTENDED_THINKING_MODELS: list[str] = [
    # Anthropic model family
    # We did not include sonnet 3.7 and 4 here as they don't brings
    # significant performance improvements for agents
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

PROMPT_CACHE_MODELS: list[str] = [
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
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]

# Models that support a top-level prompt_cache_retention parameter
# Source: OpenAI Prompt Caching docs (extended retention), which list:
#   - gpt-5.2
#   - gpt-5.1
#   - gpt-5.1-codex
#   - gpt-5.1-codex-mini
#   - gpt-5.1-chat-latest
#   - gpt-5
#   - gpt-5-codex
# Note: OpenAI docs also list gpt-4.1, but Azure rejects
# prompt_cache_retention for Azure deployments. We allow GPT-4.1
# generally (e.g., OpenAI/LiteLLM) and explicitly exclude Azure.
# Use ordered include/exclude rules (last wins) to naturally express exceptions.
PROMPT_CACHE_RETENTION_MODELS: list[str] = [
    # Broad allow for GPT-5 family (covers gpt-5.2 and variants)
    "gpt-5",
    # Allow GPT-4.1 for OpenAI/LiteLLM-style identifiers
    "gpt-4.1",
    # Exclude all mini variants by default
    "!mini",
    # Re-allow the explicitly documented supported mini variant
    "gpt-5.1-codex-mini",
    # Azure OpenAI does not support prompt_cache_retention
    "!azure/",
]

SUPPORTS_STOP_WORDS_FALSE_MODELS: list[str] = [
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
RESPONSES_API_MODELS: list[str] = [
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
FORCE_STRING_SERIALIZER_MODELS: list[str] = [
    "deepseek",  # e.g., DeepSeek-V3.2-Exp
    "glm",  # e.g., GLM-4.5 / GLM-4.6
    # Kimi K2-Instruct requires string serialization only on Groq
    "groq/kimi-k2-instruct",  # explicit provider-prefixed IDs
    # MiniMax-M2 via OpenRouter rejects array content with
    # "Input should be a valid string" for ChatCompletionToolMessage.content
    "openrouter/minimax",
]

# Models that we should send full reasoning content
# in the message input
SEND_REASONING_CONTENT_MODELS: list[str] = [
    "kimi-k2-thinking",
    "kimi-k2.5",
    "openrouter/minimax-m2",  # MiniMax-M2 via OpenRouter (interleaved thinking)
    "deepseek/deepseek-reasoner",
]


def get_features(model: str) -> ModelFeatures:
    """Get model features."""
    return ModelFeatures(
        supports_reasoning_effort=_supports_reasoning_effort(model),
        supports_extended_thinking=model_matches(model, EXTENDED_THINKING_MODELS),
        supports_prompt_cache=model_matches(model, PROMPT_CACHE_MODELS),
        supports_stop_words=not model_matches(model, SUPPORTS_STOP_WORDS_FALSE_MODELS),
        supports_responses_api=model_matches(model, RESPONSES_API_MODELS),
        force_string_serializer=model_matches(model, FORCE_STRING_SERIALIZER_MODELS),
        send_reasoning_content=model_matches(model, SEND_REASONING_CONTENT_MODELS),
        # Extended prompt_cache_retention support follows ordered include/exclude rules.
        supports_prompt_cache_retention=apply_ordered_model_rules(
            model, PROMPT_CACHE_RETENTION_MODELS
        ),
    )
