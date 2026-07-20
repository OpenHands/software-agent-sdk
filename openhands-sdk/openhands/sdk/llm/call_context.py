"""Conversation-owned context applied to LLM calls at execution time."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class LLMCallContext:
    """Immutable runtime values that follow LLM calls for one conversation.

    The context belongs to the conversation, not to an :class:`LLM`. Explicitly
    passing it to an LLM call remains the preferred path. A scoped current value
    covers internal components whose public interfaces do not yet accept a
    context, such as condensers.
    """

    prompt_cache_key: str | None = None
    session_id: str | None = None

    def for_conversation(
        self,
        conversation_id: str,
        *,
        prompt_cache_key: str | None = None,
    ) -> LLMCallContext:
        """Derive context for a conversation while preserving inheritable fields.

        Conversation identity is always replaced. Any additional fields added to
        this dataclass are inherited automatically unless this method explicitly
        gives them conversation-local semantics.
        """
        return replace(
            self,
            prompt_cache_key=prompt_cache_key or conversation_id,
            session_id=conversation_id,
        )


_CURRENT_LLM_CALL_CONTEXT: ContextVar[LLMCallContext | None] = ContextVar(
    "openhands_llm_call_context",
    default=None,
)


@contextmanager
def llm_call_context_scope(context: LLMCallContext) -> Iterator[None]:
    """Make ``context`` available to LLM calls within this execution scope."""
    token = _CURRENT_LLM_CALL_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_LLM_CALL_CONTEXT.reset(token)


def resolve_llm_call_context(
    explicit: LLMCallContext | None,
) -> LLMCallContext:
    """Resolve context using explicit, scoped, then empty precedence."""
    return explicit or _CURRENT_LLM_CALL_CONTEXT.get() or LLMCallContext()


def apply_llm_call_context(
    options: MutableMapping[str, Any],
    context: LLMCallContext,
) -> None:
    """Apply SDK-owned call context to provider request options in one place."""
    if context.prompt_cache_key:
        options["prompt_cache_key"] = context.prompt_cache_key
    if context.session_id:
        existing = options.get("extra_headers") or {}
        options["extra_headers"] = {
            **existing,
            "x-litellm-session-id": context.session_id,
        }
