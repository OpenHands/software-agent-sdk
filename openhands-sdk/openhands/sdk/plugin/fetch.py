"""Plugin fetching utilities for remote plugin sources.

Delegates to :mod:`openhands.sdk.extensions.fetch` for the actual fetch logic
and re-raises errors as :class:`PluginFetchError` to preserve the existing
public interface.
"""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.extensions.fetch import (
    ExtensionFetchError,
    fetch_with_resolution as _ext_fetch_with_resolution,
)
from openhands.sdk.git.cached_repo import GitHelper


DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "plugins"


class PluginFetchError(Exception):
    """Raised when fetching a plugin fails."""


def fetch_plugin(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> Path:
    """Fetch a plugin from a remote source and return the local cached path.

    Args:
        source: Plugin source - can be:
            - Any git URL (GitHub, GitLab, Bitbucket, Codeberg, self-hosted, etc.)
              e.g., "https://gitlab.com/org/repo", "git@bitbucket.org:team/repo.git"
            - "github:owner/repo" - GitHub shorthand (convenience syntax)
            - "/local/path" - Local path (returned as-is)
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/plugins/
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it. If False, use cached version as-is.
        repo_path: Subdirectory path within the git repository
            (e.g., 'plugins/my-plugin' for monorepos). Only relevant for git
            sources, not local paths. If specified, the returned path will
            point to this subdirectory instead of the repository root.
        git_helper: GitHelper instance (for testing). Defaults to global instance.

    Returns:
        Path to the local plugin directory (ready for Plugin.load()).
        If repo_path is specified, returns the path to that subdirectory.

    Raises:
        PluginFetchError: If fetching fails or repo_path doesn't exist.
    """
    path, _ = fetch_plugin_with_resolution(
        source=source,
        cache_dir=cache_dir,
        ref=ref,
        update=update,
        repo_path=repo_path,
        git_helper=git_helper,
    )
    return path


def fetch_plugin_with_resolution(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> tuple[Path, str | None]:
    """Fetch a plugin and return both the path and the resolved commit SHA.

    This is similar to fetch_plugin() but also returns the actual commit SHA
    that was checked out. This is useful for persistence - storing the resolved
    SHA ensures that conversation resume gets exactly the same plugin version.

    Args:
        source: Plugin source (see fetch_plugin for formats).
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/plugins/
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it. If False, use cached version as-is.
        repo_path: Subdirectory path within the git repository.
        git_helper: GitHelper instance (for testing). Defaults to global instance.

    Returns:
        Tuple of (path, resolved_ref) where:
        - path: Path to the local plugin directory
        - resolved_ref: Commit SHA that was checked out (None for local sources)

    Raises:
        PluginFetchError: If fetching fails or repo_path doesn't exist.
    """
    resolved_cache_dir = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR
    try:
        return _ext_fetch_with_resolution(
            source=source,
            cache_dir=resolved_cache_dir,
            ref=ref,
            update=update,
            repo_path=repo_path,
            git_helper=git_helper,
        )
    except ExtensionFetchError as exc:
        raise PluginFetchError("Failed to fetch plugin") from exc
