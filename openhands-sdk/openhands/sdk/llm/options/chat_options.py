from __future__ import annotations

from typing import Any

from openhands.sdk.llm.options.common import apply_defaults_if_absent
from openhands.sdk.llm.utils.model_features import (
    CLAUDE_SINGLE_SAMPLING_MODELS,
    get_features,
    model_matches,
)


def select_chat_options(
    llm, user_kwargs: dict[str, Any], has_tools: bool
) -> dict[str, Any]:
    """Behavior-preserving extraction of _normalize_call_kwargs.

    This keeps the exact provider-aware mappings and precedence.
    """
    # First pass: apply simple defaults without touching user-supplied values
    defaults: dict[str, Any] = {
        "top_k": llm.top_k,
        "top_p": llm.top_p,
        "temperature": llm.temperature,
        # OpenAI-compatible param is `max_completion_tokens`
        "max_completion_tokens": llm.max_output_tokens,
    }
    out = apply_defaults_if_absent(user_kwargs, defaults)

    # Azure -> uses max_tokens instead
    if llm.model.startswith("azure"):
        if "max_completion_tokens" in out:
            out["max_tokens"] = out.pop("max_completion_tokens")

    # If user didn't set extra_headers, propagate from llm config
    if llm.extra_headers is not None and "extra_headers" not in out:
        out["extra_headers"] = dict(llm.extra_headers)

    # Reasoning-model quirks
    if get_features(llm.model).supports_reasoning_effort:
        # LiteLLM automatically handles reasoning_effort for all models, including
        # Claude Opus 4.5 (maps to output_config and adds beta header automatically)
        if llm.reasoning_effort is not None:
            out["reasoning_effort"] = llm.reasoning_effort

        # All reasoning models ignore temp/top_p
        out.pop("temperature", None)
        out.pop("top_p", None)

        # Gemini 2.5-pro default to low if not set
        if "gemini-2.5-pro" in llm.model:
            if llm.reasoning_effort in {None, "none"}:
                out["reasoning_effort"] = "low"

    # Claude 4+ models accept temperature OR top_p, not both
    if model_matches(llm.model, CLAUDE_SINGLE_SAMPLING_MODELS):
        if "temperature" in out and "top_p" in out:
            out.pop("top_p", None)

    # Extended thinking models
    if get_features(llm.model).supports_extended_thinking:
        if llm.extended_thinking_budget:
            # Anthropic throws errors if thinking budget equals or exceeds max output
            # tokens -- force the thinking budget lower if there's a conflict
            budget_tokens = min(llm.extended_thinking_budget, llm.max_output_tokens - 1)
            out["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_tokens,
            }
            # Enable interleaved thinking
            # Merge default header with any user-provided headers; user wins on conflict
            existing = out.get("extra_headers") or {}
            out["extra_headers"] = {
                "anthropic-beta": "interleaved-thinking-2025-05-14",
                **existing,
            }
            # Fix litellm behavior
            out["max_tokens"] = llm.max_output_tokens
        # Anthropic models ignore temp/top_p
        out.pop("temperature", None)
        out.pop("top_p", None)

    # REMOVE_AT: 1.15.0 - Remove this block along with LLM.safety_settings field
    # Mistral / Gemini safety (deprecated)
    if llm.safety_settings:
        ml = llm.model.lower()
        if "mistral" in ml or "gemini" in ml:
            out["safety_settings"] = llm.safety_settings

    # Tools: if not using native, strip tool_choice so we don't confuse providers
    if not has_tools:
        out.pop("tools", None)
        out.pop("tool_choice", None)

    # Send prompt_cache_retention only if model supports it
    if (
        get_features(llm.model).supports_prompt_cache_retention
        and llm.prompt_cache_retention
    ):
        out["prompt_cache_retention"] = llm.prompt_cache_retention

    # Pass through user-provided extra_body unchanged
    if llm.litellm_extra_body:
        out["extra_body"] = llm.litellm_extra_body

    return out
