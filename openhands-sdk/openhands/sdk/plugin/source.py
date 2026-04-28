"""Source path handling — **deprecated re-exports**.

.. deprecated:: 1.17.0
    All symbols have moved to :mod:`openhands.sdk.extensions.source`.
    This module re-exports them for backward compatibility and will be
    removed in 1.22.0.
"""

from openhands.sdk.extensions.source import (
    DEFAULT_CACHE_DIR,
    GITHUB_URL_PATTERN,
    LOCAL_PREFIXES,
    GitHubURLComponents,
    is_local_path,
    parse_github_url,
    resolve_source_path,
    validate_source_path,
)


__all__ = [
    "DEFAULT_CACHE_DIR",
    "GITHUB_URL_PATTERN",
    "LOCAL_PREFIXES",
    "GitHubURLComponents",
    "is_local_path",
    "parse_github_url",
    "resolve_source_path",
    "validate_source_path",
]
