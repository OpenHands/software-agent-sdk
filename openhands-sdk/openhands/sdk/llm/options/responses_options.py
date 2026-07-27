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

    out = apply_extra_headers(out, llm)

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

    out = apply_extra_body(out, llm)
    out = apply_call_context(out, llm, call_context)

    return out
