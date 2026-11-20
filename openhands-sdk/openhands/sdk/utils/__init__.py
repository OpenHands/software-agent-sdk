"""Utility functions for the OpenHands SDK."""

from .command import sanitized_env
from .datetime import OpenHandsUUID, utc_now
from .deprecation import (
    deprecated,
    warn_deprecated,
)
from .github import sanitize_openhands_mentions
from .paging import page_iterator
from .truncate import (
    DEFAULT_TEXT_CONTENT_LIMIT,
    DEFAULT_TRUNCATE_NOTICE,
    DEFAULT_TRUNCATE_NOTICE_WITH_PERSIST,
    TruncatedTextResult,
    TruncationMetadata,
    maybe_truncate,
    truncate_with_metadata,
)


__all__ = [
    "DEFAULT_TEXT_CONTENT_LIMIT",
    "DEFAULT_TRUNCATE_NOTICE",
    "DEFAULT_TRUNCATE_NOTICE_WITH_PERSIST",
    "OpenHandsUUID",
    "TruncatedTextResult",
    "TruncationMetadata",
    "maybe_truncate",
    "truncate_with_metadata",
    "deprecated",
    "utc_now",
    "warn_deprecated",
    "sanitize_openhands_mentions",
    "page_iterator",
    "sanitized_env",
]
