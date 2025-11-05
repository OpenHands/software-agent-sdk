from dataclasses import dataclass
from fnmatch import fnmatch


def normalize_model_name(model: str) -> str:
    """Normalize a model string to a canonical, comparable name.

    Strategy:
    - Trim whitespace
    - Lowercase
    - If there is a '/', keep only the basename after the last '/'
      (handles prefixes like openrouter/, litellm_proxy/, anthropic/, etc.)
      and treat ':' inside that basename as an Ollama-style variant tag to be removed
    - There is no provider:model form; providers, when present, use 'provider/model'
    - Drop a trailing "-gguf" suffix if present
    - If basename starts with a known vendor prefix followed by '.', drop that prefix
      (e.g., 'anthropic.claude-*' -> 'claude-*')
    """
    raw = (model or "").strip().lower()
    if "/" in raw:
        name = raw.split("/")[-1]
        if ":" in name:
            # Drop Ollama-style variant tag in basename
            name = name.split(":", 1)[0]
    else:
        # No '/', keep the whole raw name (we do not support provider:model)
        name = raw

    # Drop common vendor prefixes embedded in the basename (bedrock style), once.
    # Keep this list small and explicit to avoid accidental over-matching.
    vendor_prefixes = {
        "anthropic",
        "meta",
        "cohere",
        "mistral",
        "ai21",
        "amazon",
    }
    if "." in name:
        vendor, rest = name.split(".", 1)
        if vendor in vendor_prefixes and rest:
            name = rest

    if name.endswith("-gguf"):
        name = name[: -len("-gguf")]
    return name


def model_matches(model: str, patterns: list[str]) -> bool:
    """Return True if the model matches any of the glob patterns.

    If a pattern contains a '/', it is treated as provider-qualified and matched
    against the full, lowercased model string (including provider prefix).
    Otherwise, it is matched against the normalized basename.
    """
    raw = (model or "").strip().lower()
    name = normalize_model_name(model)
    for pat in patterns:
        pat_l = pat.lower()
        if "/" in pat_l:
            if fnmatch(raw, pat_l):
                return True
        else:
            if fnmatch(name, pat_l):
                return True
    return False


@dataclass(frozen=True)
class ModelFeatures:
    supports_function_calling: bool
    supports_reasoning_effort: bool
    supports_extended_thinking: bool
    supports_prompt_cache: bool
    supports_stop_words: bool
    supports_responses_api: bool


# Pattern tables capturing current behavior. Keep patterns lowercase.
FUNCTION_CALLING_PATTERNS: list[str] = [
    # Anthropic families
    "claude-3-7-sonnet*",
    "claude-3.7-sonnet*",
    "claude-sonnet-3-7-latest",
    "claude-3-5-sonnet*",
    "claude-3.5-haiku*",
    "claude-3-5-haiku*",
    "claude-sonnet-4*",
    "claude-haiku-4*",
    "claude-opus-4*",
    # OpenAI families
    "gpt-4o*",
    "gpt-4.1",
    "gpt-5*",
    # o-series (keep exact o1 support per existing list)
    "o1-2024-12-17",
    "o3*",
    "o4-mini",
    # Google Gemini
    "gemini-2.5-pro",
    # Others
    "kimi-k2-0711-preview",
    "kimi-k2-instruct",
    "qwen3-coder*",
    "qwen3-coder-480b-a35b-instruct",
]

REASONING_EFFORT_PATTERNS: list[str] = [
    # Mirror main behavior exactly (no unintended expansion)
    "o1-2024-12-17",
    "o1*",  # Match all o1 variants including o1-preview
    "o3*",  # Match all o3 variants
    "o3-2025-04-16",
    "o3-mini-2025-01-31",
    "o3-mini",
    "o4-mini",
    "o4-mini-2025-04-16",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    # OpenAI GPT-5 family (includes mini variants)
    "gpt-5*",
]

EXTENDED_THINKING_PATTERNS: list[str] = [
    # Anthropic model family
    # We did not include sonnet 3.7 and 4 here as they don't brings
    # significant performance improvements for agents
    "claude-sonnet-4-5*",
    "claude-haiku-4-5*",
]

PROMPT_CACHE_PATTERNS: list[str] = [
    "claude-3-7-sonnet*",
    "claude-3.7-sonnet*",
    "claude-sonnet-3-7-latest",
    "claude-3-5-sonnet*",
    "claude-3.5-sonnet*",
    "claude-3-5-haiku*",
    "claude-3.5-haiku*",
    "claude-3-haiku-20240307*",
    "claude-3-opus-20240229*",
    "claude-sonnet-4*",
    "claude-opus-4*",
]

SUPPORTS_STOP_WORDS_FALSE_PATTERNS: list[str] = [
    # o1 family doesn't support stop words
    "o1*",
    # grok-4 specific model name (basename)
    "grok-4-0709",
    "grok-code-fast-1",
    # DeepSeek R1 family
    "deepseek-r1-0528*",
]

# Models that should use the OpenAI Responses API path by default
RESPONSES_API_PATTERNS: list[str] = [
    # OpenAI GPT-5 family (includes mini variants)
    "gpt-5*",
]


def get_features(model: str) -> ModelFeatures:
    return ModelFeatures(
        supports_function_calling=model_matches(model, FUNCTION_CALLING_PATTERNS),
        supports_reasoning_effort=model_matches(model, REASONING_EFFORT_PATTERNS),
        supports_extended_thinking=model_matches(model, EXTENDED_THINKING_PATTERNS),
        supports_prompt_cache=model_matches(model, PROMPT_CACHE_PATTERNS),
        supports_stop_words=not model_matches(
            model, SUPPORTS_STOP_WORDS_FALSE_PATTERNS
        ),
        supports_responses_api=model_matches(model, RESPONSES_API_PATTERNS),
    )
