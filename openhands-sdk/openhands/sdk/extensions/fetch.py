"""Fetching utilities for extensions."""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.git.cached_repo import GitHelper, try_cached_clone_or_update
from openhands.sdk.git.utils import extract_repo_name, is_git_url, normalize_git_url
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class ExtensionFetchError(Exception):
    """Raised when fetching an extension fails."""


def fetch(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> Path:
    """Fetch an extension from a source and return the local path.

    Args:
        source: Extension source -- git URL, GitHub shorthand, or local path.
        cache_dir: Directory for caching.
        ref: Optional branch, tag, or commit to checkout.
        update: If true and cache exists, update it.
        repo_path: Subdirectory path within the repository.
        git_helper: GitHelper instance (for testing).

    Returns:
        Path to the local extension directory.
    """
    path, _ = fetch_with_resolution(
        source=source,
        cache_dir=cache_dir,
        ref=ref,
        update=update,
        repo_path=repo_path,
        git_helper=git_helper,
    )
    return path


def fetch_with_resolution(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> tuple[Path, str | None]:
    """Fetch an extension and return both the path and resolved commit SHA.

    Args:
        source: Extension source (git URL, GitHub shorthand, or local path).
        cache_dir: Directory for caching.
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it.
        repo_path: Subdirectory path within the repository.
        git_helper: GitHelper instance (for testing).

    Returns:
        Tuple of (path, resolved_ref) where resolved_ref is the commit SHA for git
        sources and None for local paths.

    Raises:
        ExtensionFetchError: If fetching the extension fails.
    """
    raise NotImplementedError()
