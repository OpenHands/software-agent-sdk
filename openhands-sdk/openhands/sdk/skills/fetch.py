"""Skill fetching utilities for AgentSkills sources."""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.git.cached_repo import GitHelper
from openhands.sdk.plugin.fetch import PluginFetchError, fetch_plugin_with_resolution


DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "skills"


class SkillFetchError(Exception):
    """Raised when fetching a skill fails."""


def fetch_skill(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> Path:
    """Fetch a skill from a source and return the local path.

    Args:
        source: Skill source - git URL, GitHub shorthand, or local path.
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/skills/.
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it.
        repo_path: Subdirectory path within the repository.
        git_helper: GitHelper instance (for testing).

    Returns:
        Path to the local skill directory.
    """
    path, _ = fetch_skill_with_resolution(
        source=source,
        cache_dir=cache_dir,
        ref=ref,
        update=update,
        repo_path=repo_path,
        git_helper=git_helper,
    )
    return path


def fetch_skill_with_resolution(
    source: str,
    cache_dir: Path | None = None,
    ref: str | None = None,
    update: bool = True,
    repo_path: str | None = None,
    git_helper: GitHelper | None = None,
) -> tuple[Path, str | None]:
    """Fetch a skill and return both the path and resolved commit SHA.

    Args:
        source: Skill source (git URL, GitHub shorthand, or local path).
        cache_dir: Directory for caching. Defaults to ~/.openhands/cache/skills/.
        ref: Optional branch, tag, or commit to checkout.
        update: If True and cache exists, update it.
        repo_path: Subdirectory path within the repository.
        git_helper: GitHelper instance (for testing).

    Returns:
        Tuple of (path, resolved_ref) where resolved_ref is the commit SHA for git
        sources and None for local paths.

    Raises:
        SkillFetchError: If fetching the skill fails.
    """
    resolved_cache_dir = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR
    try:
        return fetch_plugin_with_resolution(
            source=source,
            cache_dir=resolved_cache_dir,
            ref=ref,
            update=update,
            repo_path=repo_path,
            git_helper=git_helper,
        )
    except PluginFetchError as exc:
        raise SkillFetchError(str(exc)) from exc
