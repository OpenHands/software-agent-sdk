"""Persistence configuration public API.

This package re-exports the supported persistence configuration knobs (constants
and helpers) to provide a small, stable import surface:

- Encapsulation: internal module layout can change without breaking callers.
- Discoverability: callers can find persistence settings via
  ``openhands.sdk.persistence``.
- Consistency: matches the SDK pattern of exposing intended entry points at the
  package level rather than requiring deep imports.

Anything exported via ``__all__`` should be treated as part of the supported SDK
API.
"""

from .settings import INLINE_CONTEXT_KEY, INLINE_ENV_VAR, should_inline_conversations


__all__ = [
    "INLINE_CONTEXT_KEY",
    "INLINE_ENV_VAR",
    "should_inline_conversations",
]
