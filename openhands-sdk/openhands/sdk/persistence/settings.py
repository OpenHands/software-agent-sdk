"""Shared helpers for SDK persistence configuration."""

from __future__ import annotations

import os


INLINE_ENV_VAR = "OPENHANDS_INLINE_CONVERSATIONS"
INLINE_CONTEXT_KEY = "inline_llm_persistence"
_FALSE_VALUES = {"0", "false", "no"}


def should_inline_conversations() -> bool:
    """Return True when conversations should be persisted with inline LLM payloads."""

    value = os.getenv(INLINE_ENV_VAR, "true").strip().lower()
    return value not in _FALSE_VALUES
