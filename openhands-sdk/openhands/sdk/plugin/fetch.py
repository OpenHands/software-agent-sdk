"""Plugin fetching utilities for remote plugin sources."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from openhands.sdk.git.cached_repo import GitHelper, cached_clone_or_update
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "plugins"


class PluginFetchError(Exception):
    """Raised when fetching a plugin fails."""

    pass


def parse_plugin_source(source: str) -> tuple[str, str]:
    """Parse plugin source into (type, url).

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
            raise PluginFetchError(
                f"Invalid GitHub shorthand format: {source}. "
                f"Expected format: github:owner/repo"
            )
        url = f"https://github.com/{repo_path}.git"
        return ("github", url)

    # Git URLs: detect by protocol/scheme rather than enumerating providers
    # This handles GitHub, GitLab, Bitbucket, Codeberg, self-hosted instances, etc.
    if _is_git_url(source):
        url = _normalize_git_url(source)
        return ("git", url)

    # Local path: starts with /, ~, . or contains / without a URL scheme
    if source.startswith(("/", "~", ".")):
        return ("local", source)

    if "/" in source and "://" not in source:
        # Relative path like "plugins/my-plugin"
        return ("local", source)

    raise PluginFetchError(
        f"Unable to parse plugin source: {source}. "
        f"Expected formats: 'github:owner/repo', git URL, or local path"
    )


def _is_git_url(source: str) -> bool:
    """Check if a source string looks like a git URL.

    Detects git URLs by their protocol/scheme rather than enumerating providers.
    This handles any git hosting service (GitHub, GitLab, Codeberg, self-hosted, etc.)
    """
    # HTTPS/HTTP URLs to git repositories
    if source.startswith(("https://", "http://")):
        return True

    # SSH format: git@host:path or user@host:path
    if re.match(r"^[\w.-]+@[\w.-]+:", source):
        return True

    # Git protocol
    if source.startswith("git://"):
        return True

    # File protocol (for testing)
    if source.startswith("file://"):
        return True

    return False


def _normalize_git_url(url: str) -> str:
    """Normalize a git URL by ensuring .git suffix for HTTPS URLs."""
    if url.startswith(("https://", "http://")) and not url.endswith(".git"):
        url = url.rstrip("/")
        url = f"{url}.git"
    return url


def get_cache_path(source: str, cache_dir: Path | None = None) -> Path:
    """Get the cache path for a plugin source.

    Creates a deterministic path based on a hash of the source URL.

    Args:
        source: The plugin source (URL or path).
        cache_dir: Base cache directory. Defaults to ~/.openhands/cache/plugins/

    Returns:
        Path where the plugin should be cached.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    # Create a hash of the source for the directory name
    source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]

    # Also include a readable portion of the source
    # Extract repo name from various formats
    readable_name = _extract_readable_name(source)

    cache_name = f"{readable_name}-{source_hash}"
    return cache_dir / cache_name


def _extract_readable_name(source: str) -> str:
    """Extract a human-readable name from a source URL/path.

    Extracts the last path component (repo name) and sanitizes it.
    The hash in get_cache_path() ensures uniqueness, so this just needs
    to be human-readable for debugging/browsing cache directories.

    Args:
        source: Plugin source string (URL or path).

    Returns:
        A sanitized name suitable for use in directory names (max 32 chars).
    """
    # Strip common prefixes to get to the path portion
    name = source
    for prefix in ("github:", "https://", "http://", "git://", "file://"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break

    # Handle SSH format: user@host:path -> path
    if "@" in name and ":" in name and "/" not in name.split(":")[0]:
        name = name.split(":", 1)[1]

    # Remove .git suffix and get last path component
    name = name.rstrip("/").removesuffix(".git")
    name = name.rsplit("/", 1)[-1]

    # Sanitize: keep alphanumeric, dash, underscore only
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")

    return name[:32] if name else "plugin"


def _resolve_local_source(url: str, subpath: str | None) -> Path:
    """Resolve a local plugin source to a path.

    Args:
        url: Local path string (may contain ~ for home directory).
        subpath: Optional subdirectory within the local path.

    Returns:
        Resolved absolute path to the plugin directory.

    Raises:
        PluginFetchError: If path doesn't exist or subpath is invalid.
    """
    local_path = Path(url).expanduser().resolve()
    if not local_path.exists():
        raise PluginFetchError(f"Local plugin path does not exist: {local_path}")
    return _apply_subpath(local_path, subpath, "local plugin path")


def _fetch_remote_source(
    url: str,
    cache_dir: Path,
    ref: str | None,
    update: bool,
    subpath: str | None,
    git_helper: GitHelper | None,
    source: str,
) -> Path:
    """Fetch a remote plugin source and cache it locally.

    Args:
        url: Git URL to fetch.
        cache_dir: Base directory for caching.
        ref: Optional branch, tag, or commit to checkout.
        update: Whether to update existing cache.
        subpath: Optional subdirectory within the repository.
        git_helper: GitHelper instance for git operations.
        source: Original source string (for error messages).

    Returns:
        Path to the cached plugin directory.

    Raises:
        PluginFetchError: If fetching fails or subpath is invalid.
    """
    plugin_path = get_cache_path(url, cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = cached_clone_or_update(
        url=url,
        repo_path=plugin_path,
        ref=ref,
        update=update,
        git_helper=git_helper,
    )

    if result is None:
        raise PluginFetchError(f"Failed to fetch plugin from {source}")

    return _apply_subpath(plugin_path, subpath, "plugin repository")


def _apply_subpath(base_path: Path, subpath: str | None, context: str) -> Path:
    """Apply a subpath to a base path, validating it exists.

    Args:
        base_path: The root path.
        subpath: Optional subdirectory path (may have leading/trailing slashes).
        context: Description for error messages (e.g., "plugin repository").

    Returns:
        The final path (base_path if no subpath, otherwise base_path/subpath).

    Raises:
        PluginFetchError: If subpath doesn't exist.
    """
    if not subpath:
        return base_path

    final_path = base_path / subpath.strip("/")
    if not final_path.exists():
        raise PluginFetchError(f"Subdirectory '{subpath}' not found in {context}")
    return final_path


def fetch_plugin(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    subpath: str | None = None,
    git_helper: GitHelper | None = None,
) -> Path:
    """Fetch a plugin from a remote source and return the local cached path.

    Args:
        source: Plugin source - can be:
            - "github:owner/repo" - GitHub repository shorthand
            - "https://github.com/owner/repo.git" - Full git URL
            - "/local/path" - Local path (returned as-is)
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/plugins/
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it. If False, use cached version as-is.
        subpath: Optional subdirectory path within the repo. If specified, the returned
            path will point to this subdirectory instead of the repository root.
        git_helper: GitHelper instance (for testing). Defaults to global instance.

    Returns:
        Path to the local plugin directory (ready for Plugin.load()).
        If subpath is specified, returns the path to that subdirectory.

    Raises:
        PluginFetchError: If fetching fails or subpath doesn't exist.
    """
    source_type, url = parse_plugin_source(source)

    if source_type == "local":
        return _resolve_local_source(url, subpath)

    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    return _fetch_remote_source(
        url, cache_dir, ref, update, subpath, git_helper, source
    )
