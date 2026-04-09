"""Extension fetching utilities for remote sources.

This module provides the shared fetching infrastructure used by plugins,
skills, and other installable extensions.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from openhands.sdk.git.cached_repo import GitHelper, try_cached_clone_or_update
from openhands.sdk.git.utils import extract_repo_name, is_git_url, normalize_git_url
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "extensions"


class ExtensionFetchError(Exception):
    """Raised when fetching an extension fails."""


def parse_extension_source(source: str) -> tuple[str, str]:
    """Parse extension source into (type, url).

    Args:
        source: Extension source string. Can be:
            - "github:owner/repo" - GitHub repository shorthand
            - "https://github.com/owner/repo.git" - Full git URL
            - "git@github.com:owner/repo.git" - SSH git URL
            - "/local/path" - Local path

    Returns:
        Tuple of (source_type, normalized_url) where source_type is one of:
        - "github": GitHub repository
        - "git": Any git URL
        - "local": Local filesystem path

    Raises:
        ExtensionFetchError: If the source format is not recognized.

    Examples:
        >>> parse_extension_source("github:owner/repo")
        ("github", "https://github.com/owner/repo.git")
        >>> parse_extension_source("https://gitlab.com/org/repo.git")
        ("git", "https://gitlab.com/org/repo.git")
        >>> parse_extension_source("/local/path")
        ("local", "/local/path")
    """
    source = source.strip()

    # GitHub shorthand: github:owner/repo
    if source.startswith("github:"):
        repo_path = source[7:]  # Remove "github:" prefix
        if "/" not in repo_path or repo_path.count("/") > 1:
            raise ExtensionFetchError(
                f"Invalid GitHub shorthand format: {source}. "
                f"Expected format: github:owner/repo"
            )
        url = f"https://github.com/{repo_path}.git"
        return ("github", url)

    # Git URLs: detect by protocol/scheme rather than enumerating providers
    if is_git_url(source):
        url = normalize_git_url(source)
        return ("git", url)

    # Local path: starts with /, ~, . or contains / without a URL scheme
    if source.startswith(("/", "~", ".")):
        return ("local", source)

    if "/" in source and "://" not in source:
        # Relative path like "extensions/my-ext"
        return ("local", source)

    raise ExtensionFetchError(
        f"Unable to parse extension source: {source}. "
        f"Expected formats: 'github:owner/repo', git URL, or local path"
    )


def get_cache_path(source: str, cache_dir: Path | None = None) -> Path:
    """Get the cache path for an extension source.

    Creates a deterministic path based on a hash of the source URL.

    Args:
        source: The extension source (URL or path).
        cache_dir: Base cache directory. Defaults to ~/.openhands/cache/extensions/

    Returns:
        Path where the extension should be cached.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
    readable_name = extract_repo_name(source)
    cache_name = f"{readable_name}-{source_hash}"
    return cache_dir / cache_name


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
        context: Description for error messages (e.g., "extension repository").

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
        resolved_ref = ref or "HEAD"

    final_path = _apply_subpath(repo_cache_path, subpath, "extension repository")
    return final_path, resolved_ref


def fetch_extension(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> Path:
    """Fetch an extension from a source and return the local cached path.

    Args:
        source: Extension source - can be:
            - Any git URL (GitHub, GitLab, Bitbucket, Codeberg, self-hosted, etc.)
            - "github:owner/repo" - GitHub shorthand (convenience syntax)
            - "/local/path" - Local path (returned as-is)
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/extensions/
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it. If False, use cached as-is.
        repo_path: Subdirectory path within the git repository.
        git_helper: GitHelper instance (for testing). Defaults to global instance.

    Returns:
        Path to the local extension directory.

    Raises:
        ExtensionFetchError: If fetching fails or repo_path doesn't exist.
    """
    path, _ = fetch_extension_with_resolution(
        source=source,
        cache_dir=cache_dir,
        ref=ref,
        update=update,
        repo_path=repo_path,
        git_helper=git_helper,
    )
    return path


def fetch_extension_with_resolution(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> tuple[Path, str | None]:
    """Fetch an extension and return both the path and resolved commit SHA.

    This is similar to fetch_extension() but also returns the actual commit SHA
    that was checked out. This is useful for persistence - storing the resolved
    SHA ensures that conversation resume gets exactly the same version.

    Args:
        source: Extension source (see fetch_extension for formats).
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/extensions/
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it. If False, use cached as-is.
        repo_path: Subdirectory path within the git repository.
        git_helper: GitHelper instance (for testing). Defaults to global instance.

    Returns:
        Tuple of (path, resolved_ref) where:
        - path: Path to the local extension directory
        - resolved_ref: Commit SHA that was checked out (None for local sources)

    Raises:
        ExtensionFetchError: If fetching fails or repo_path doesn't exist.
    """
    source_type, url = parse_extension_source(source)

    if source_type == "local":
        if repo_path is not None:
            raise ExtensionFetchError(
                f"repo_path is not supported for local extension sources. "
                f"Specify the full path directly instead of "
                f"source='{source}' + repo_path='{repo_path}'"
            )
        return _resolve_local_source(url), None

    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    git = git_helper if git_helper is not None else GitHelper()

    return _fetch_remote_source_with_resolution(
        url, cache_dir, ref, update, repo_path, git, source
    )
