from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM, LLMCallContext


def apply_defaults_if_absent(
    user_kwargs: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    """Return a new dict with defaults applied when keys are absent.

    - Pure and deterministic; does not mutate inputs
    - Only applies defaults when the key is missing and default is not None
    - Does not alter user-provided values
    """
    out = dict(user_kwargs)
    for key, value in defaults.items():
        if key not in out and value is not None:
            out[key] = value
    return out


def apply_extra_headers(user_kwargs: dict[str, Any], llm: LLM) -> dict[str, Any]:
    """Return a new dict with the LLM's extra headers applied.

    Propagates ``llm.extra_headers`` when the caller did not set its own, then
    merges the OpenRouter attribution headers. These go through ``extra_headers``
    rather than ``os.environ`` so they don't leak across conversations in a
    multi-tenant server (see issue #3138). User-supplied headers win.

    - Pure and deterministic; does not mutate inputs
    """
    out = dict(user_kwargs)

    if llm.extra_headers is not None and "extra_headers" not in out:
        out["extra_headers"] = dict(llm.extra_headers)

    openrouter_headers = llm._openrouter_headers()
    if openrouter_headers:
        existing = out.get("extra_headers") or {}
        out["extra_headers"] = {**openrouter_headers, **existing}

    return out


def apply_extra_body(user_kwargs: dict[str, Any], llm: LLM) -> dict[str, Any]:
    """Return a new dict with the user-provided ``extra_body`` passed through.

    - Pure and deterministic; does not mutate inputs
    """
    out = dict(user_kwargs)
    if llm.litellm_extra_body:
        out["extra_body"] = llm.litellm_extra_body
    return out


def apply_call_context(
    user_kwargs: dict[str, Any],
    llm: LLM,
    call_context: LLMCallContext | None,
) -> dict[str, Any]:
    """Return a new dict with per-conversation call context applied (#3443).

    Prefers the explicitly threaded context and falls back to the LLM's own
    PrivateAttr for callers that don't thread one (e.g. the condenser's
    dedicated LLM).

    - Pure and deterministic; does not mutate inputs
    """
    out = dict(user_kwargs)

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
