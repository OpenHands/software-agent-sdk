"""Repository cloning and management utilities for OpenHands Cloud workspace.

This module re-exports utilities from the SDK's repo module for backward
compatibility. The canonical implementation now lives in
openhands.sdk.workspace.repo.

.. deprecated::
    Import directly from openhands.sdk.workspace.repo or
    openhands.workspace.cloud (which re-exports from SDK).
"""

# Re-export everything from SDK repo module for backward compatibility
from openhands.sdk.workspace.repo import (
    CLONE_TIMEOUT,
    PROVIDER_TOKEN_NAMES,
    PROVIDER_URL_PATTERNS,
    CloneResult,
    GitProvider,
    RepoMapping,
    RepoSource,
    TokenFetcher,
    _build_clone_url,
    _detect_provider_from_url,
    _extract_repo_name,
    _get_unique_dir_name,
    _is_commit_sha,
    _is_short_url_format,
    _mask_token,
    _mask_url,
    _sanitize_dir_name,
    clone_repos,
    get_repos_context,
)


__all__ = [
    "CLONE_TIMEOUT",
    "PROVIDER_TOKEN_NAMES",
    "PROVIDER_URL_PATTERNS",
    "CloneResult",
    "GitProvider",
    "RepoMapping",
    "RepoSource",
    "TokenFetcher",
    "_build_clone_url",
    "_detect_provider_from_url",
    "_extract_repo_name",
    "_get_unique_dir_name",
    "_is_commit_sha",
    "_is_short_url_format",
    "_mask_token",
    "_mask_url",
    "_sanitize_dir_name",
    "clone_repos",
    "get_repos_context",
]
