from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk.llm.options.common import apply_defaults_if_absent
from openhands.sdk.llm.utils.model_features import get_features


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext


def select_responses_options(
    llm,
    user_kwargs: dict[str, Any],
    *,
    include: list[str] | None,
    store: bool | None,
    call_context: LLMCallContext | None = None,
) -> dict[str, Any]:
    """Behavior-preserving extraction of _normalize_responses_kwargs."""
    # Apply defaults for keys that are not forced by policy
    # Note: max_output_tokens is not supported in subscription mode
    defaults = {}
    if not llm.is_subscription:
        defaults["max_output_tokens"] = llm.effective_max_output_tokens
    out = apply_defaults_if_absent(user_kwargs, defaults)

    # Enforce sampling/tool behavior for Responses path
    # Note: temperature is not supported in subscription mode
    if not llm.is_subscription:
        out["temperature"] = 1.0
    out["tool_choice"] = "auto"

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

    # Store defaults to False (stateless) unless explicitly provided
    if store is not None:
        out["store"] = bool(store)
    else:
        out.setdefault("store", False)

    model_features = get_features(llm._model_name_for_capabilities())

    # Include encrypted reasoning only when the user enables it on the LLM,
    # and only for stateless calls (store=False). Respect user choice.
    # Note: include and reasoning are not supported in subscription mode
    # (the Codex subscription endpoint silently returns empty output when
    # these parameters are present).
    if not llm.is_subscription:
        include_list = list(include) if include is not None else []
        supports_reasoning = model_features.supports_reasoning_effort

        if (
            not out.get("store", False)
            and llm.enable_encrypted_reasoning
            and supports_reasoning
        ):
            if "reasoning.encrypted_content" not in include_list:
                include_list.append("reasoning.encrypted_content")
        if include_list:
            out["include"] = include_list

        if llm.reasoning_effort and supports_reasoning:
            out["reasoning"] = {"effort": llm.reasoning_effort}
            # Optionally include summary if explicitly set (requires verified org)
            if llm.reasoning_summary:
                out["reasoning"]["summary"] = llm.reasoning_summary

    # Send prompt_cache_retention only if model supports it
    # Note: prompt_cache_retention is not supported in subscription mode
    if (
        not llm.is_subscription
        and model_features.supports_prompt_cache_retention
        and llm.prompt_cache_retention
    ):
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
    if ctx.extra_headers:
        existing = out.get("extra_headers") or {}
        out["extra_headers"] = {
            **existing,
            **ctx.extra_headers,
        }
    if ctx.session_id:
        existing = out.get("extra_headers") or {}
        out["extra_headers"] = {
            **existing,
            "x-litellm-session-id": ctx.session_id,
        }

    return out
