"""Source path handling for marketplace plugins and skills.

Supports local paths (./path, /path, ~/path, file:///path) and
GitHub URLs (https://github.com/{owner}/{repo}/blob/{branch}/{path}).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from openhands.sdk.git.cached_repo import try_cached_clone_or_update
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.path import is_local_path_source


logger = get_logger(__name__)

GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
    r"(?:blob|tree)/(?P<branch>[^/]+)/(?P<path>.+)$"
)
DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "git"


class GitHubURLComponents(NamedTuple):
    """Parsed components of a GitHub blob/tree URL."""

    owner: str
    repo: str
    branch: str
    path: str


def parse_github_url(url: str) -> GitHubURLComponents | None:
    """Parse GitHub URL into components, or None if not a valid GitHub URL."""
    if match := GITHUB_URL_PATTERN.match(url):
        return GitHubURLComponents(
            match.group("owner"),
            match.group("repo"),
            match.group("branch"),
            match.group("path"),
        )
    return None


def is_local_path(source: str) -> bool:
    """Check if source is a local path (./, ../, /, ~, file://)."""
    return is_local_path_source(source)


def validate_source_path(source: str) -> str:
    """Validate source path format. Raises ValueError if invalid."""
    if is_local_path(source) or parse_github_url(source):
        return source
    raise ValueError(
        f"Invalid source path: {source!r}. Must be local path or GitHub URL."
    )


def resolve_source_path(
    source: str,
    base_path: Path | None = None,
    cache_dir: Path | None = None,
    update: bool = True,
) -> Path | None:
    """Resolve source path to absolute local path.

    Args:
        source: Source path string (local path, file:// URL, or GitHub URL).
        base_path: Base directory for resolving relative paths.
        cache_dir: Directory for caching cloned GitHub repos.
        update: Whether to update cached repos (git pull).

    Returns:
        Resolved absolute Path, or None if GitHub clone/update fails.
        Callers should handle None gracefully (e.g., skip with warning).

    Supported source formats:
        - Local paths: ./path, ../path, /absolute, ~/home
        - file:// URLs: file:///absolute/path
        - GitHub URLs: https://github.com/{owner}/{repo}/blob/{branch}/{path}
    """
    # Handle file:// URLs
    if source.startswith("file://"):
        return Path(source[7:])

    # Handle GitHub URLs
    if gh := parse_github_url(source):
        cache = cache_dir or DEFAULT_CACHE_DIR
        repo_path = cache / "github.com" / gh.owner.lower() / gh.repo.lower()
        clone_url = f"https://github.com/{gh.owner}/{gh.repo}.git"

        if try_cached_clone_or_update(clone_url, repo_path, gh.branch, update):
            return repo_path / gh.path
        logger.warning(f"Failed to clone/update: {source}")
        return None

    # Handle local paths
    if source.startswith("/"):
        return Path(source)

    path = Path(source).expanduser()
    if path.is_absolute():
        return path
    if base_path:
        return (base_path / path).resolve()
    return path.resolve()
