from __future__ import annotations

from typing import Any

from openhands.sdk.llm.options.common import apply_defaults_if_absent


def select_responses_options(
    llm,
    user_kwargs: dict[str, Any],
    *,
    include: list[str] | None,
    store: bool | None,
) -> dict[str, Any]:
    """Behavior-preserving extraction of _normalize_responses_kwargs."""
    # Check if user explicitly tried to set temperature for Responses API
    if "temperature" in user_kwargs:
        raise ValueError(
            "The 'temperature' parameter is not supported by the SDK for "
            "Responses API. While temperature is officially supported by "
            "OpenAI's Responses API, the SDK only routes reasoning models "
            "(GPT-5, Codex) to this API, and these models do not "
            "support temperature settings."
        )

    # Apply defaults for keys that are not forced by policy
    out = apply_defaults_if_absent(
        user_kwargs,
        {
            "max_output_tokens": llm.max_output_tokens,
        },
    )

    # Enforce sampling/tool behavior for Responses path
    # Temperature defaults to None for Responses API (not set at all)
    out["tool_choice"] = "auto"

    # Store defaults to False (stateless) unless explicitly provided
    if store is not None:
        out["store"] = bool(store)
    else:
        out.setdefault("store", False)

    # Include encrypted reasoning if stateless
    include_list = list(include) if include is not None else []
    if not out.get("store", False):
        if "reasoning.encrypted_content" not in include_list:
            include_list.append("reasoning.encrypted_content")
    if include_list:
        out["include"] = include_list

    # Request plaintext reasoning summary
    effort = llm.reasoning_effort or "high"
    out["reasoning"] = {"effort": effort, "summary": "detailed"}

    return out
