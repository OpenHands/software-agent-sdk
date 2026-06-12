from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from litellm import get_supported_openai_params

from openhands.sdk.llm.utils.openhands_provider import OPENHANDS_PROVIDER_PREFIX


def model_matches(model: str, patterns: Iterable[str]) -> bool:
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
    # True when the model's API rejects http(s) image URLs and only accepts
    # base64 ``data:`` URLs. See REQUIRES_INLINE_IMAGE_DATA_MODELS.
    requires_inline_image_data: bool


LITELLM_PROXY_PREFIX = "litellm_proxy/"

# Common deployment path prefixes used in LiteLLM proxy configurations
DEPLOYMENT_PREFIXES = ("prod/", "dev/", "staging/", "test/")


@cache
def _normalized_supported_openai_params(model: str | None) -> frozenset[str]:
    """Return LiteLLM-supported OpenAI params for a normalized model name."""
    if not model:
        return frozenset()

    normalized = model.strip().lower()
    for provider_prefix in (LITELLM_PROXY_PREFIX, OPENHANDS_PROVIDER_PREFIX):
        if normalized.startswith(provider_prefix):
            normalized = normalized.removeprefix(provider_prefix)
            break

    # Strip deployment prefixes (e.g., "prod/", "dev/", "staging/", "test/")
    for prefix in DEPLOYMENT_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break

    params = get_supported_openai_params(
        model=normalized,
        custom_llm_provider=None,
    )
    return frozenset(params or ())


# SDK-side override allowlist for models that support the ``reasoning_effort``
# parameter but are not (yet) recognized by LiteLLM's
# ``get_supported_openai_params`` registry. Without this, brand-new model ids
# fall through to the non-reasoning branch in ``chat_options.py`` and the SDK
# leaves ``temperature``/``top_p`` in the request, which providers like
# Anthropic now reject for these models with
# ``temperature is deprecated for this model``.
#
# Entries should be removed once the corresponding LiteLLM release ships
# metadata for the model.
REASONING_EFFORT_MODELS: list[str] = [
    # https://www.anthropic.com/news/claude-fable-5
    "claude-fable-5",
]


def _supports_reasoning_effort(model: str | None) -> bool:
    """Return True if LiteLLM or our override list says the model accepts
    ``reasoning_effort``.

    The override list (``REASONING_EFFORT_MODELS``) lets us recognize new
    reasoning models before LiteLLM's metadata catches up, so the chat-options
    layer can strip ``temperature``/``top_p`` (and forward ``reasoning_effort``)
    before the request reaches the provider.
    """
    if model_matches(model or "", REASONING_EFFORT_MODELS):
        return True
    return "reasoning_effort" in _normalized_supported_openai_params(model)


EXTENDED_THINKING_MODELS: list[str] = [
    # Anthropic Claude models with useful agent performance gains.
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
    # Anthropic Claude 4 variants (official IDs use hyphens)
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    # https://www.anthropic.com/news/claude-fable-5
    # Listed explicitly until LiteLLM metadata recognizes it.
    "claude-fable-5",
    # Do NOT add Gemini: explicit cache_control markers freeze its cache at the
    # static prefix and disable Google's implicit caching on the growing body
    # (~6-14x cost). Gemini uses implicit prefix caching instead.
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
    "kimi-k2.6",
    "openrouter/minimax-m2",  # MiniMax-M2 via OpenRouter (interleaved thinking)
    "deepseek/deepseek-reasoner",
    "deepseek/deepseek-v4-pro",  # Dual-mode (Thinking/Non-Thinking)
    "deepseek/deepseek-v4-flash",  # Dual-mode (Thinking/Non-Thinking)
]

# Models whose API rejects http(s) image URLs and only accepts base64
# ``data:`` URLs (or vendor-specific file IDs). When this matches, the SDK
# fetches each image URL and inlines it as ``data:{mime};base64,...`` before
# sending. Only includes models where this restriction has been verified in
# production runs (see issue #3155 for kimi-k2.6).
#
# NOTE: This is intentionally narrow. The same provider can host the same
# model behind different upstreams that DO accept URLs (e.g.
# bedrock/moonshotai.kimi-k2.5, fireworks_ai/.../kimi-k2.6), so we match on
# the specific model id, not on the provider name.
REQUIRES_INLINE_IMAGE_DATA_MODELS: tuple[str, ...] = (
    # Moonshot public Kimi API: https://platform.kimi.ai/docs/guide/use-kimi-vision-model
    # > URL-formatted images: Not supported, currently only supports
    # > base64-encoded image content and images/videos uploaded via file ID
    "moonshot/kimi-k2.6",
)


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
        requires_inline_image_data=model_matches(
            model, REQUIRES_INLINE_IMAGE_DATA_MODELS
        ),
    )
