"""Curated lists of models that are known to work well with OpenHands.

These lists feed the model pickers (agent-server ``/llm/verified-models``) and
the ``openhands/`` provider allowlist. They are meant to be short.

Rule: for each model line, keep only the two latest versions. Variants of a kept
version (``-pro``, ``-mini``, ``-codex``, ``-flash``, dated aliases) stay with it.
Unversioned "current" aliases (for example ``deepseek-chat``, ``kimi-for-coding``)
stay. When a new version lands, drop the oldest one in the same line.
"""

# GPT: gpt-6 and gpt-5.6. Codex: gpt-5.3-codex and gpt-5.2-codex.
VERIFIED_OPENAI_MODELS = [
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-6-astra",
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
]

# Opus: 5.5 and 5. Sonnet: 5 and 4.6. Haiku: 4.5. Fable: 5.1 and 5.
VERIFIED_ANTHROPIC_MODELS = [
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-fable-5-1",
    "claude-fable-5",
]

# Devstral 2512 (two sizes).
VERIFIED_MISTRAL_MODELS = [
    "devstral-2512",
    "devstral-medium-2512",
]

# Pro: 3.1. Flash: 3.8 and 3.7. Flash-lite: 3.5 and 3.1.
VERIFIED_GEMINI_MODELS = [
    "gemini-3.1-pro",
    "gemini-3.1-pro-preview",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

# V4 and V3.2; ``deepseek-chat`` is the current alias.
VERIFIED_DEEPSEEK_MODELS = [
    "deepseek-chat",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
    "deepseek-v4.1-flash",
    "deepseek-v3.2-reasoner",
]

# Kimi K3 and K2.7; ``kimi-for-coding`` is Moonshot's own alias (direct API only;
# the OpenHands proxy does not serve it, so it is not in the openhands list).
VERIFIED_MOONSHOT_MODELS = [
    "kimi-k3",
    "kimi-k2.7-code",
    "kimi-for-coding",
]

# M3 and M2.7.
VERIFIED_MINIMAX_MODELS = [
    "minimax-m3",
    "minimax-m2.7",
]

# GLM 5.3 and 5.2.
VERIFIED_GLM_MODELS = [
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
]

# Nemotron 3.5 and 3.
VERIFIED_NVIDIA_MODELS = [
    "nemotron-3.5-lightning-30b-a3b",
    "nemotron-3-nano",
    "nemotron-3-nano-omni-30b-a3b-reasoning",
    "nemotron-3-super-120b-a12b",
    "nemotron-3-ultra-550b-a55b",
]

# Plus: 3.7 and 3.6. Max: 3.8 and 3.7. Flash: 3.8 and 3.7. Coder: unversioned.
VERIFIED_QWEN_MODELS = [
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.8-max",
    "qwen3.7-max",
    "qwen3.8-flash",
    "qwen3.7-flash",
    "qwen3-coder-480b",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
]

# What the ``openhands/`` provider serves. Same rule; every entry must also be in
# a provider list above, except OpenHands-only models.
VERIFIED_OPENHANDS_MODELS = [
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-fable-5-1",
    "claude-fable-5",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-6-astra",
    "gpt-5.6",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "minimax-m3",
    "minimax-m2.7",
    "gemini-3.1-pro",
    "gemini-3.1-pro-preview",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    "deepseek-chat",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
    "deepseek-v4.1-flash",
    "deepseek-v3.2-reasoner",
    "kimi-k3",
    "kimi-k2.7-code",
    "devstral-2512",
    "devstral-medium-2512",
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "nemotron-3.5-lightning-30b-a3b",
    "nemotron-3-nano",
    "nemotron-3-super-120b-a12b",
    "nemotron-3-ultra-550b-a55b",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.8-max",
    "qwen3.7-max",
    "qwen3.8-flash",
    "qwen3.7-flash",
    "qwen3-coder-480b",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
    "trinity-large-thinking",
]


VERIFIED_MODELS = {
    "openhands": VERIFIED_OPENHANDS_MODELS,
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
