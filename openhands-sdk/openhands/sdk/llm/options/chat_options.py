from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk.llm.options.common import (
    apply_call_context,
    apply_defaults_if_absent,
    apply_extra_body,
    apply_extra_headers,
)
from openhands.sdk.llm.utils.model_features import get_features


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext


def select_chat_options(
    llm,
    user_kwargs: dict[str, Any],
    has_tools: bool,
    call_context: LLMCallContext | None = None,
) -> dict[str, Any]:
    """Behavior-preserving extraction of _normalize_call_kwargs.

    This keeps the exact provider-aware mappings and precedence.
    """
    # First pass: apply simple defaults without touching user-supplied values
    max_output_tokens = llm.effective_max_output_tokens
    defaults: dict[str, Any] = {
        "top_k": llm.top_k,
        "top_p": llm.top_p,
        "temperature": llm.temperature,
        # OpenAI-compatible param is `max_completion_tokens`
        "max_completion_tokens": max_output_tokens,
    }
    out = apply_defaults_if_absent(user_kwargs, defaults)

    # Azure -> uses max_tokens instead
    if llm.model.startswith("azure"):
        if "max_completion_tokens" in out:
            out["max_tokens"] = out.pop("max_completion_tokens")

    out = apply_extra_headers(out, llm)

    # Reasoning-model quirks
    supports_reasoning_effort = get_features(llm.model).supports_reasoning_effort
    if supports_reasoning_effort:
        # LiteLLM automatically handles reasoning_effort for all models, including
        # Claude Opus 4.5 (maps to output_config and adds beta header automatically)
        if llm.reasoning_effort is not None:
            out["reasoning_effort"] = llm.reasoning_effort

        # All reasoning models ignore temp/top_p, except Gemini
        if "gemini" not in llm.model.lower():
            out.pop("temperature", None)
            out.pop("top_p", None)

    # Extended thinking models
    if get_features(llm.model).supports_extended_thinking:
        if llm.extended_thinking_budget and max_output_tokens:
            # Anthropic throws errors if thinking budget equals or exceeds max output
            # tokens -- force the thinking budget lower if there's a conflict
            budget_tokens = min(
                llm.extended_thinking_budget,
                max_output_tokens - 1,
            )
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
            out["max_tokens"] = max_output_tokens
        # Anthropic models ignore temp/top_p
        out.pop("temperature", None)
        out.pop("top_p", None)

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

    out = apply_extra_body(out, llm)
    out = apply_call_context(out, llm, call_context)

    return out
