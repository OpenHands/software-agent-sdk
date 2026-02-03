#!/usr/bin/env python3
"""
Resolve model IDs to full model configurations.

Reads:
- MODEL_IDS: comma-separated model IDs

Outputs to GITHUB_OUTPUT:
- models_json: JSON array of full model configs with display names
"""

import json
import os
import sys


# Model configurations dictionary
MODELS = {
    "claude-sonnet-4-5-20250929": {
        "id": "claude-sonnet-4-5-20250929",
        "display_name": "Claude Sonnet 4.5",
        "llm_config": {
            "model": "litellm_proxy/claude-sonnet-4-5-20250929",
            "temperature": 0.0,
        },
    },
    "kimi-k2-thinking": {
        "id": "kimi-k2-thinking",
        "display_name": "Kimi K2 Thinking",
        "llm_config": {"model": "litellm_proxy/moonshot/kimi-k2-thinking"},
    },
    # https://www.kimi.com/blog/kimi-k2-5.html
    "kimi-k2.5": {
        "id": "kimi-k2.5",
        "display_name": "Kimi K2.5",
        "llm_config": {
            "model": "litellm_proxy/moonshot/kimi-k2.5",
            "temperature": 1.0,
            "top_p": 0.95,
        },
    },
    # https://www.alibabacloud.com/help/en/model-studio/deep-thinking
    "qwen3-max-thinking": {
        "id": "qwen3-max-thinking",
        "display_name": "Qwen3 Max Thinking",
        "llm_config": {
            "model": "litellm_proxy/dashscope/qwen3-max-2026-01-23",
            "litellm_extra_body": {"enable_thinking": True},
        },
    },
    "claude-4.5-opus": {
        "id": "claude-4.5-opus",
        "display_name": "Claude 4.5 Opus",
        "llm_config": {
            "model": "litellm_proxy/anthropic/claude-opus-4-5-20251101",
            "temperature": 0.0,
        },
    },
    "gemini-3-pro": {
        "id": "gemini-3-pro",
        "display_name": "Gemini 3 Pro",
        "llm_config": {"model": "litellm_proxy/gemini/gemini-3-pro-preview"},
    },
    "gemini-3-flash": {
        "id": "gemini-3-flash",
        "display_name": "Gemini 3 Flash",
        "llm_config": {"model": "litellm_proxy/gemini/gemini-3-flash-preview"},
    },
    "gpt-5.2": {
        "id": "gpt-5.2",
        "display_name": "GPT-5.2",
        "llm_config": {"model": "litellm_proxy/openai/gpt-5.2-2025-12-11"},
    },
    "gpt-5.2-codex": {
        "id": "gpt-5.2-codex",
        "display_name": "GPT-5.2 Codex",
        "llm_config": {"model": "litellm_proxy/gpt-5.2-codex"},
    },
    "gpt-5.2-high-reasoning": {
        "id": "gpt-5.2-high-reasoning",
        "display_name": "GPT-5.2 High Reasoning",
        "llm_config": {
            "model": "litellm_proxy/openai/gpt-5.2-2025-12-11",
            "reasoning_effort": "high",
        },
    },
    "minimax-m2": {
        "id": "minimax-m2",
        "display_name": "MiniMax M2",
        "llm_config": {"model": "litellm_proxy/minimax/minimax-m2"},
    },
    "minimax-m2.1": {
        "id": "minimax-m2.1",
        "display_name": "MiniMax M2.1",
        "llm_config": {"model": "litellm_proxy/minimax/MiniMax-M2.1"},
    },
    "deepseek-v3.2-reasoner": {
        "id": "deepseek-v3.2-reasoner",
        "display_name": "DeepSeek V3.2 Reasoner",
        "llm_config": {"model": "litellm_proxy/deepseek/deepseek-reasoner"},
    },
    "qwen-3-coder": {
        "id": "qwen-3-coder",
        "display_name": "Qwen 3 Coder",
        "llm_config": {
            "model": "litellm_proxy/fireworks_ai/qwen3-coder-480b-a35b-instruct"
        },
    },
    "nemotron-3-nano-30b": {
        "id": "nemotron-3-nano-30b",
        "display_name": "NVIDIA Nemotron 3 Nano 30B",
        "llm_config": {
            "model": "litellm_proxy/openai/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8",
            "temperature": 0.0,
        },
    },
    "glm-4.7": {
        "id": "glm-4.7",
        "display_name": "GLM-4.7",
        "llm_config": {"model": "litellm_proxy/openrouter/z-ai/glm-4.7"},
    },
}


def error_exit(msg: str, exit_code: int = 1) -> None:
    """Print error message and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def get_required_env(key: str) -> str:
    """Get required environment variable or exit with error."""
    value = os.environ.get(key)
    if not value:
        error_exit(f"{key} not set")
    return value


def find_models_by_id(model_ids: list[str]) -> list[dict]:
    """Find models by ID. Fails fast on missing ID.

    Args:
        model_ids: List of model IDs to find

    Returns:
        List of model dictionaries matching the IDs

    Raises:
        SystemExit: If any model ID is not found
    """
    resolved = []
    for model_id in model_ids:
        if model_id not in MODELS:
            available = ", ".join(sorted(MODELS.keys()))
            error_exit(
                f"Model ID '{model_id}' not found. Available models: {available}"
            )
        resolved.append(MODELS[model_id])
    return resolved


def main() -> None:
    model_ids_str = get_required_env("MODEL_IDS")
    github_output = get_required_env("GITHUB_OUTPUT")

    # Parse requested model IDs
    model_ids = [mid.strip() for mid in model_ids_str.split(",") if mid.strip()]

    # Resolve model configs
    resolved = find_models_by_id(model_ids)

    # Output as JSON
    models_json = json.dumps(resolved, separators=(",", ":"))
    with open(github_output, "a", encoding="utf-8") as f:
        f.write(f"models_json={models_json}\n")

    print(f"Resolved {len(resolved)} model(s): {', '.join(model_ids)}")


if __name__ == "__main__":
    main()
