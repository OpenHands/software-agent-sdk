VERIFIED_OPENAI_MODELS = [
    "gpt-5.4",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5-codex",
    "gpt-5-2025-08-07",
    "gpt-5-mini-2025-08-07",
    "o4-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-32k",
    "gpt-4.1",
    "gpt-4.1-2025-04-14",
    "o1-mini",
    "o3",
    "codex-mini-latest",
]

VERIFIED_ANTHROPIC_MODELS = [
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-5-20251101",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-opus-4-1-20250805",
    "claude-3-7-sonnet-20250219",
    "claude-3-sonnet-20240229",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
    "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
]

VERIFIED_MISTRAL_MODELS = [
    "devstral-small-2505",
    "devstral-small-2507",
    "devstral-medium-2507",
    "devstral-2512",
    "devstral-medium-2512",
]

VERIFIED_GEMINI_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro",
    "gemini-3-flash",
    "gemini-3-pro",
]

VERIFIED_DEEPSEEK_MODELS = [
    "deepseek-chat",
    "deepseek-v3.2-reasoner",
]

VERIFIED_MOONSHOT_MODELS = [
    "kimi-k2-thinking",
    "kimi-k2.5",
]

VERIFIED_MINIMAX_MODELS = [
    "minimax-m2.1",
    "minimax-m2.5",
    "minimax-m2.7",
]

VERIFIED_GLM_MODELS = [
    "glm-4.7",
    "glm-5",
    "glm-5.1",
]

VERIFIED_NVIDIA_MODELS = [
    "nemotron-3-nano",
    "nemotron-3-super",
]

VERIFIED_QWEN_MODELS = [
    "qwen3-6-plus",
    "qwen3-coder-480b",
]

_PROVIDER_VERIFIED_MODELS = {
    "anthropic": VERIFIED_ANTHROPIC_MODELS,
    "openai": VERIFIED_OPENAI_MODELS,
    "mistral": VERIFIED_MISTRAL_MODELS,
    "gemini": VERIFIED_GEMINI_MODELS,
    "deepseek": VERIFIED_DEEPSEEK_MODELS,
    "moonshot": VERIFIED_MOONSHOT_MODELS,
    "minimax": VERIFIED_MINIMAX_MODELS,
    "glm": VERIFIED_GLM_MODELS,
    "nvidia": VERIFIED_NVIDIA_MODELS,
    "qwen": VERIFIED_QWEN_MODELS,
}

# The OpenHands provider aggregates every model verified in any upstream provider,
# so adding a model to any provider list automatically makes it available via
# OpenHands as well.  Do NOT maintain a separate manual list here.
VERIFIED_OPENHANDS_MODELS = sorted(
    {m for models in _PROVIDER_VERIFIED_MODELS.values() for m in models}
)

VERIFIED_MODELS = {"openhands": VERIFIED_OPENHANDS_MODELS, **_PROVIDER_VERIFIED_MODELS}
