from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk.llm.options.common import apply_defaults_if_absent


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

    model_features = llm._model_features()

    # Reasoning is expressed in provider-neutral terms. LiteLLM owns the
    # provider-specific translation (for example adaptive Anthropic thinking).
    supports_reasoning_effort = model_features.supports_reasoning_effort
    if supports_reasoning_effort:
        # LiteLLM automatically handles reasoning_effort for all models, including
        # Claude Opus 4.5 (maps to output_config and adds beta header automatically)
        if llm.reasoning_effort is not None:
            out["reasoning_effort"] = llm.reasoning_effort

    model_name = llm._model_name_for_capabilities()
    if model_features.supports_sampling_params is False or (
        supports_reasoning_effort and "gemini" not in model_name.lower()
    ):
        # Sampling compatibility is a single capability. Keep all three
        # related parameters together so a newly restricted model cannot fail
        # because top_k survived while temperature/top_p were removed.
        out.pop("temperature", None)
        out.pop("top_p", None)
        out.pop("top_k", None)

    # Preserve legacy manual thinking only for models that metadata (or an
    # explicit override) does not identify as adaptive.
    if model_features.thinking_mode == "manual":
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
        out.pop("top_k", None)

    # Tools: if not using native, strip tool_choice so we don't confuse providers
    if not has_tools:
        out.pop("tools", None)
        out.pop("tool_choice", None)

    # Send prompt_cache_retention only if model supports it
    if model_features.supports_prompt_cache_retention and llm.prompt_cache_retention:
        out["prompt_cache_retention"] = llm.prompt_cache_retention

    # Pass through user-provided extra_body unchanged
    if llm.litellm_extra_body:
        out["extra_body"] = llm.litellm_extra_body

    # Inject per-conversation state from call context (#3443).
    # Prefer explicitly threaded context; fall back to PrivateAttr for
    # callers that don't thread (e.g. condenser's dedicated LLM).
    ctx = call_context or llm._call_context
    if ctx.prompt_cache_key:
        out["prompt_cache_key"] = ctx.prompt_cache_key
    if ctx.session_id:
        existing = out.get("extra_headers") or {}
        out["extra_headers"] = {
            **existing,
            "x-litellm-session-id": ctx.session_id,
        }

    return out
