from __future__ import annotations

from typing import Any

from openhands.sdk.llm.options.common import apply_defaults_if_absent
from openhands.sdk.llm.utils.model_features import get_features


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

    # Reasoning-model quirks
    if get_features(llm.model).supports_reasoning_effort:
        # Preferred: use reasoning_effort
        if llm.reasoning_effort is not None:
            out["reasoning_effort"] = llm.reasoning_effort
        # Anthropic/OpenAI reasoning models ignore temp/top_p
        out.pop("temperature", None)
        out.pop("top_p", None)
        # Gemini 2.5-pro default to low if not set
        if "gemini-2.5-pro" in llm.model:
            if llm.reasoning_effort in {None, "none"}:
                out["reasoning_effort"] = "low"

    # Extended thinking models
    if get_features(llm.model).supports_extended_thinking:
        if llm.extended_thinking_budget:
            out["thinking"] = {
                "type": "enabled",
                "budget_tokens": llm.extended_thinking_budget,
            }
            # Enable interleaved thinking
            out["extra_headers"] = {"anthropic-beta": "interleaved-thinking-2025-05-14"}
            # Fix litellm behavior
            out["max_tokens"] = llm.max_output_tokens
        # Anthropic models ignore temp/top_p
        out.pop("temperature", None)
        out.pop("top_p", None)

    # Mistral / Gemini safety
    if llm.safety_settings:
        ml = llm.model.lower()
        if "mistral" in ml or "gemini" in ml:
            out["safety_settings"] = llm.safety_settings

    # Tools: if not using native, strip tool_choice so we don't confuse providers
    if not has_tools:
        out.pop("tools", None)
        out.pop("tool_choice", None)

    # non litellm proxy special-case: keep `extra_body` off unless model requires it
    if "litellm_proxy" not in llm.model:
        out.pop("extra_body", None)

    return out
