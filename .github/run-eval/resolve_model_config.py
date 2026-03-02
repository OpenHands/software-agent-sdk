#!/usr/bin/env python3
"""
Resolve model IDs to full model configurations and verify model availability.

Reads:
- MODEL_IDS: comma-separated model IDs
- LLM_API_KEY: API key for litellm_proxy (optional, for preflight check)
- LLM_BASE_URL: Base URL for litellm_proxy (optional, defaults to eval proxy)
- SKIP_PREFLIGHT: Set to 'true' to skip the preflight LLM check

Outputs to GITHUB_OUTPUT:
- models_json: JSON array of full model configs with display names
"""

import json
import os
import sys
from typing import Any


# SDK-specific parameters that should not be passed to litellm.
# These parameters are used by the SDK's LLM wrapper but are not part of litellm's API.
# Keep this list in sync with SDK LLM config parameters that are SDK-internal.
SDK_ONLY_PARAMS = {"disable_vision"}


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
    "qwen3.5-flash": {
        "id": "qwen3.5-flash",
        "display_name": "Qwen3.5 Flash",
        "llm_config": {
            "model": "litellm_proxy/dashscope/qwen3.5-flash-2026-02-23",
            "temperature": 0.0,
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
    "claude-4.6-opus": {
        "id": "claude-4.6-opus",
        "display_name": "Claude 4.6 Opus",
        "llm_config": {
            "model": "litellm_proxy/anthropic/claude-opus-4-6",
            "temperature": 0.0,
        },
    },
    "claude-sonnet-4-6": {
        "id": "claude-sonnet-4-6",
        "display_name": "Claude Sonnet 4.6",
        "llm_config": {
            "model": "litellm_proxy/anthropic/claude-sonnet-4-6",
            "temperature": 0.0,
        },
    },
    "gemini-3-pro": {
        "id": "gemini-3-pro",
        "display_name": "Gemini 3 Pro",
        "llm_config": {"model": "litellm_proxy/gemini-3-pro-preview"},
    },
    "gemini-3-flash": {
        "id": "gemini-3-flash",
        "display_name": "Gemini 3 Flash",
        "llm_config": {"model": "litellm_proxy/gemini-3-flash-preview"},
    },
    "gemini-3.1-pro": {
        "id": "gemini-3.1-pro",
        "display_name": "Gemini 3.1 Pro",
        "llm_config": {"model": "litellm_proxy/gemini-3.1-pro-preview"},
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
    "minimax-m2.5": {
        "id": "minimax-m2.5",
        "display_name": "MiniMax M2.5",
        "llm_config": {
            "model": "litellm_proxy/minimax/MiniMax-M2.5",
            "temperature": 1.0,
            "top_p": 0.95,
        },
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
        "llm_config": {
            "model": "litellm_proxy/openrouter/z-ai/glm-4.7",
            # OpenRouter glm-4.7 is text-only despite LiteLLM reporting vision support
            "disable_vision": True,
        },
    },
    "glm-5": {
        "id": "glm-5",
        "display_name": "GLM-5",
        "llm_config": {
            "model": "litellm_proxy/openrouter/z-ai/glm-5",
            # OpenRouter glm-5 is text-only despite LiteLLM reporting vision support
            "disable_vision": True,
        },
    },
    "qwen3-coder-next": {
        "id": "qwen3-coder-next",
        "display_name": "Qwen3 Coder Next",
        "llm_config": {"model": "litellm_proxy/openrouter/qwen/qwen3-coder-next"},
    },
    "qwen3-coder-30b-a3b-instruct": {
        "id": "qwen3-coder-30b-a3b-instruct",
        "display_name": "Qwen3 Coder 30B A3B Instruct",
        "llm_config": {"model": "litellm_proxy/Qwen3-Coder-30B-A3B-Instruct"},
    },
    "gpt-oss-20b": {
        "id": "gpt-oss-20b",
        "display_name": "GPT OSS 20B",
        "llm_config": {"model": "litellm_proxy/gpt-oss-20b"},
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


def check_model(
    model_config: dict[str, Any],
    api_key: str,
    base_url: str,
    timeout: int = 60,
) -> tuple[bool, str]:
    """Check a single model with a simple completion request using litellm.

    Args:
        model_config: Model configuration dict with 'llm_config' key
        api_key: API key for authentication
        base_url: Base URL for the LLM proxy
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success: bool, message: str)
    """
    import litellm

    llm_config = model_config.get("llm_config", {})
    model_name = llm_config.get("model", "unknown")
    display_name = model_config.get("display_name", model_name)

    try:
        # Build kwargs from llm_config, excluding 'model' and SDK-specific params
        kwargs = {
            k: v
            for k, v in llm_config.items()
            if k != "model" and k not in SDK_ONLY_PARAMS
        }

        # Use simple arithmetic prompt that works reliably across all models
        # max_tokens=100 provides enough room for models to respond
        # (some need >10 tokens)
        response = litellm.completion(
            model=model_name,
            messages=[{"role": "user", "content": "1+1="}],
            max_tokens=100,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            **kwargs,
        )

        content = response.choices[0].message.content if response.choices else None

        if content:
            return True, f"✓ {display_name}: OK"
        else:
            # Check if there's any other data in the response for diagnostics
            finish_reason = (
                response.choices[0].finish_reason if response.choices else None
            )
            usage = getattr(response, "usage", None)
            return (
                False,
                (
                    f"✗ {display_name}: Empty response "
                    f"(finish_reason={finish_reason}, usage={usage})"
                ),
            )

    except litellm.exceptions.Timeout:
        return False, f"✗ {display_name}: Request timed out after {timeout}s"
    except litellm.exceptions.APIConnectionError as e:
        return False, f"✗ {display_name}: Connection error - {e}"
    except litellm.exceptions.BadRequestError as e:
        return False, f"✗ {display_name}: Bad request - {e}"
    except litellm.exceptions.NotFoundError as e:
        return False, f"✗ {display_name}: Model not found - {e}"
    except Exception as e:
        return False, f"✗ {display_name}: {type(e).__name__} - {e}"


def run_preflight_check(models: list[dict[str, Any]]) -> bool:
    """Run preflight LLM check for all models.

    Args:
        models: List of model configurations to test

    Returns:
        True if all models passed, False otherwise
    """
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://llm-proxy.eval.all-hands.dev")
    skip_preflight = os.environ.get("SKIP_PREFLIGHT", "").lower() == "true"

    if skip_preflight:
        print("Preflight check: SKIPPED (SKIP_PREFLIGHT=true)")
        return True

    if not api_key:
        print("Preflight check: SKIPPED (LLM_API_KEY not set)")
        return True

    print(f"\nPreflight LLM check for {len(models)} model(s)...")
    print("-" * 50)

    all_passed = True
    for model_config in models:
        success, message = check_model(model_config, api_key, base_url)
        print(message)
        if not success:
            all_passed = False

    print("-" * 50)

    if all_passed:
        print(f"✓ All {len(models)} model(s) passed preflight check\n")
    else:
        print("✗ Some models failed preflight check")
        print("Evaluation aborted to avoid wasting compute resources.\n")

    return all_passed


def main() -> None:
    model_ids_str = get_required_env("MODEL_IDS")
    github_output = get_required_env("GITHUB_OUTPUT")

    # Parse requested model IDs
    model_ids = [mid.strip() for mid in model_ids_str.split(",") if mid.strip()]

    # Resolve model configs
    resolved = find_models_by_id(model_ids)
    print(f"Resolved {len(resolved)} model(s): {', '.join(model_ids)}")

    # Run preflight check
    if not run_preflight_check(resolved):
        error_exit("Preflight LLM check failed")

    # Output as JSON
    models_json = json.dumps(resolved, separators=(",", ":"))
    with open(github_output, "a", encoding="utf-8") as f:
        f.write(f"models_json={models_json}\n")


if __name__ == "__main__":
    main()
