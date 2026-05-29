from __future__ import annotations

from typing import Any

from openhands.sdk.llm.options.common import apply_defaults_if_absent
from openhands.sdk.llm.utils.model_features import get_features, model_matches


# Models that are listed in EXTENDED_THINKING_MODELS for the side effect of
# stripping `temperature`/`top_p` (which Anthropic reasoning models reject),
# but that do NOT accept the legacy
# ``thinking: {"type": "enabled", "budget_tokens": N}`` block.
#
# claude-opus-4-8 uses Anthropic's newer adaptive reasoning schema
# (``thinking: {"type": "adaptive"}`` + ``output_config.effort``) and returns
# a 400 ``"thinking.type.enabled" is not supported for this model`` if we send
# the legacy block. Until the SDK builds the new schema, just skip emitting
# the legacy block for these models; temp/top_p stripping still runs below.
_MODELS_WITHOUT_LEGACY_THINKING_BLOCK: list[str] = [
    "claude-opus-4-8",
]


def select_chat_options(
    llm, user_kwargs: dict[str, Any], has_tools: bool
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

    # If user didn't set extra_headers, propagate from llm config
    if llm.extra_headers is not None and "extra_headers" not in out:
        out["extra_headers"] = dict(llm.extra_headers)

    # Inject OpenRouter HTTP-Referer / X-Title via extra_headers so we don't
    # have to mutate os.environ (which would leak across conversations in a
    # multi-tenant server; see issue #3138). User-supplied headers win.
    openrouter_headers = llm._openrouter_headers()
    if openrouter_headers:
        existing = out.get("extra_headers") or {}
        out["extra_headers"] = {**openrouter_headers, **existing}

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
        skip_legacy_thinking_block = model_matches(
            llm.model, _MODELS_WITHOUT_LEGACY_THINKING_BLOCK
        )
        if (
            not skip_legacy_thinking_block
            and llm.extended_thinking_budget
            and max_output_tokens
        ):
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

    # Pass through user-provided extra_body unchanged
    if llm.litellm_extra_body:
        out["extra_body"] = llm.litellm_extra_body

    if llm._prompt_cache_key:
        out["prompt_cache_key"] = llm._prompt_cache_key

    return out
