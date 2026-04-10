"""Fetching utilities for extensions."""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path

from openhands.sdk.git.cached_repo import GitHelper, try_cached_clone_or_update
from openhands.sdk.git.utils import extract_repo_name, is_git_url, normalize_git_url
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class ExtensionFetchError(Exception):
    """Raised when fetching an extension fails."""


class SourceType(str, Enum):
    LOCAL = "local"
    GIT = "git"
    GITHUB = "github"


def parse_extension_source(source: str) -> tuple[SourceType, str]:
    """Parse extension source into (SourceType, url).

    Args:
        source: Plugin source string. Can be:
            - "github:owner/repo" - GitHub repository shorthand
            - "https://github.com/owner/repo.git" - Full git URL
            - "git@github.com:owner/repo.git" - SSH git URL
            - "/local/path" - Local path

    Returns:
        Tuple of (source_type, normalized_url) where source_type is one of:
        - "github": GitHub repository
        - "git": Any git URL
        - "local": Local filesystem path

    Examples:
        >>> parse_plugin_source("github:owner/repo")
        ("github", "https://github.com/owner/repo.git")
        >>> parse_plugin_source("https://gitlab.com/org/repo.git")
        ("git", "https://gitlab.com/org/repo.git")
        >>> parse_plugin_source("/local/path")
        ("local", "/local/path")
    """
    source = source.strip()

    # GitHub shorthand: github:owner/repo
    if source.startswith("github:"):
        repo_path = source[7:]  # Remove "github:" prefix
        # Validate format
        if "/" not in repo_path or repo_path.count("/") > 1:
            raise ExtensionFetchError(
                f"Invalid GitHub shorthand format: {source}. "
                f"Expected format: github:owner/repo"
            )
        url = f"https://github.com/{repo_path}.git"
        return (SourceType.GITHUB, url)

    # Git URLs: detect by protocol/scheme rather than enumerating providers
    # This handles GitHub, GitLab, Bitbucket, Codeberg, self-hosted instances, etc.
    if is_git_url(source):
        url = normalize_git_url(source)
        return (SourceType.GIT, url)

    # Local path: starts with /, ~, . or contains / without a URL scheme
    if source.startswith(("/", "~", ".")):
        return (SourceType.LOCAL, source)

    if "/" in source and "://" not in source:
        # Relative path like "plugins/my-plugin"
        return (SourceType.LOCAL, source)

    raise ExtensionFetchError(
        f"Unable to parse extension source: {source}. "
        f"Expected formats: 'github:owner/repo', git URL, or local path"
    )


def _resolve_local_source(url: str) -> Path:
    """Resolve a local extension source to a path.

    Args:
        url: Local path string (may contain ~ for home directory).

    Returns:
        Resolved absolute path to the extension directory.

    Raises:
        ExtensionFetchError: If path doesn't exist.
    """
    local_path = Path(url).expanduser().resolve()
    if not local_path.exists():
        raise ExtensionFetchError(f"Local extension path does not exist: {local_path}")
    return local_path


def _apply_subpath(base_path: Path, subpath: str | None, context: str) -> Path:
    """Apply a subpath to a base path, validating it exists.

    Args:
        base_path: The root path.
        subpath: Optional subdirectory path (may have leading/trailing slashes).
        context: Description for error messages (e.g., "plugin repository").

    Returns:
        The final path (base_path if no subpath, otherwise base_path/subpath).

    Raises:
        ExtensionFetchError: If subpath doesn't exist.
    """
    if not subpath:
        return base_path

    final_path = base_path / subpath.strip("/")
    if not final_path.exists():
        raise ExtensionFetchError(f"Subdirectory '{subpath}' not found in {context}")
    return final_path


def fetch(
    source: str,
    cache_dir: Path,
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
    cache_dir: Path,
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
    source_type, url = parse_extension_source(source)

    if source_type == SourceType.LOCAL:
        if repo_path is not None:
            raise ExtensionFetchError(
                f"repo_path is not supported for local extension sources. "
                f"Specify the full path directly instead of "
                f"source='{source}' + repo_path='{repo_path}'"
            )
        return _resolve_local_source(url), None

    git = git_helper if git_helper is not None else GitHelper()

    plugin_path, resolved_ref = _fetch_remote_source_with_resolution(
        url, cache_dir, ref, update, repo_path, git, source
    )
    return plugin_path, resolved_ref


def get_cache_path(source: str, cache_dir: Path) -> Path:
    """Get the cache path for a plugin source.

    Creates a deterministic path based on a hash of the source URL.

    Args:
        source: The plugin source (URL or path).
        cache_dir: Base cache directory.

    Returns:
        Path where the plugin should be cached.
    """
    # Create a hash of the source for the directory name
    source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]

    # Extract repo name for human-readable cache directory name
    readable_name = extract_repo_name(source)

    cache_name = f"{readable_name}-{source_hash}"
    return cache_dir / cache_name


def _fetch_remote_source_with_resolution(
    url: str,
    cache_dir: Path,
    ref: str | None,
    update: bool,
    subpath: str | None,
    git_helper: GitHelper,
    source: str,
) -> tuple[Path, str]:
    """Fetch a remote extension source and return path + resolved commit SHA.

    Args:
        url: Git URL to fetch.
        cache_dir: Base directory for caching.
        ref: Optional branch, tag, or commit to checkout.
        update: Whether to update existing cache.
        subpath: Optional subdirectory within the repository.
        git_helper: GitHelper instance for git operations.
        source: Original source string (for error messages).

    Returns:
        Tuple of (path, resolved_ref) where resolved_ref is the commit SHA.

    Raises:
        ExtensionFetchError: If fetching fails or subpath is invalid.
    """
    repo_cache_path = get_cache_path(url, cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = try_cached_clone_or_update(
        url=url,
        repo_path=repo_cache_path,
        ref=ref,
        update=update,
        git_helper=git_helper,
    )

    if result is None:
        raise ExtensionFetchError(f"Failed to fetch extension from {source}")

    # Get the actual commit SHA that was checked out
    try:
        resolved_ref = git_helper.get_head_commit(repo_cache_path)
    except Exception as e:
        logger.warning(f"Could not get commit SHA for {source}: {e}")
        # Fall back to the requested ref if we can't get the SHA
        resolved_ref = ref or "HEAD"

    final_path = _apply_subpath(repo_cache_path, subpath, "extension repository")
    return final_path, resolved_ref
