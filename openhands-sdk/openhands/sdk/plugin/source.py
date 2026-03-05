"""Source path handling for marketplace plugins and skills.

This module provides utilities for parsing and resolving source paths in
marketplace.json files. Source paths can be:

1. Local file paths (relative or absolute): ./skills/my-skill, /path/to/skill
2. File URLs: file:///path/to/skill
3. GitHub blob URLs: https://github.com/{owner}/{repo}/blob/{branch}/{path}

For GitHub URLs, the repository is cloned/cached locally and the path to the
file/directory within the cache is returned.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from openhands.sdk.git.cached_repo import GitHelper, try_cached_clone_or_update
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Pattern for GitHub blob URLs
# Matches: https://github.com/{owner}/{repo}/blob/{branch}/{path}
# Also matches tree URLs: https://github.com/{owner}/{repo}/tree/{branch}/{path}
GITHUB_BLOB_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
    r"(?:blob|tree)/(?P<branch>[^/]+)/(?P<path>.+)$"
)

# Default cache directory for git repositories
DEFAULT_CACHE_DIR = Path.home() / ".openhands" / "cache" / "git"


class GitHubURLComponents(NamedTuple):
    """Parsed components of a GitHub blob/tree URL."""

    owner: str
    repo: str
    branch: str
    path: str


def parse_github_url(url: str) -> GitHubURLComponents | None:
    """Parse a GitHub blob/tree URL into its components.

    Args:
        url: A GitHub URL like https://github.com/owner/repo/blob/branch/path/to/file

    Returns:
        GitHubURLComponents if the URL matches, None otherwise.

    Examples:
        >>> url = "https://github.com/OpenHands/extensions/blob/main/skills/github"
        >>> parse_github_url(url)
        GitHubURLComponents(owner='OpenHands', repo='extensions', ...)

        >>> parse_github_url("./local/path")
        None
    """
    match = GITHUB_BLOB_PATTERN.match(url)
    if not match:
        return None

    return GitHubURLComponents(
        owner=match.group("owner"),
        repo=match.group("repo"),
        branch=match.group("branch"),
        path=match.group("path"),
    )


def is_local_path(source: str) -> bool:
    """Check if a source string represents a local path.

    A source is considered local if it:
    - Starts with ./ or ../
    - Starts with / (absolute path)
    - Starts with ~ (home directory)
    - Starts with file://

    Args:
        source: Source path string to check.

    Returns:
        True if the source is a local path, False otherwise.
    """
    return (
        source.startswith("./")
        or source.startswith("../")
        or source.startswith("/")
        or source.startswith("~")
        or source.startswith("file://")
    )


def is_github_url(source: str) -> bool:
    """Check if a source string is a GitHub blob/tree URL.

    Args:
        source: Source path string to check.

    Returns:
        True if the source is a GitHub blob/tree URL, False otherwise.
    """
    return parse_github_url(source) is not None


def validate_source_path(source: str) -> str:
    """Validate that a source path follows the allowed patterns.

    Allowed patterns:
    1. Local file paths: ./, ../, /, ~, file://
    2. GitHub blob URLs: https://github.com/{owner}/{repo}/blob/{branch}/{path}
    3. GitHub tree URLs: https://github.com/{owner}/{repo}/tree/{branch}/{path}

    Args:
        source: Source path string to validate.

    Returns:
        The validated source string (unchanged).

    Raises:
        ValueError: If the source doesn't match any allowed pattern.
    """
    if is_local_path(source):
        return source

    if is_github_url(source):
        return source

    raise ValueError(
        f"Invalid source path: {source!r}. "
        "Source must be one of:\n"
        "  - Local path: ./path, ../path, /absolute/path, ~/path, file:///path\n"
        "  - GitHub URL: https://github.com/{owner}/{repo}/blob/{branch}/{path}"
    )


class SourcePath(str):
    """A validated source path string.

    This is a custom string type that validates the source path format on creation.
    It can be used as a Pydantic field type for automatic validation.

    Supported formats:
    - Local paths: ./path, ../path, /absolute/path, ~/path, file:///path
    - GitHub URLs: https://github.com/{owner}/{repo}/blob/{branch}/{path}

    Examples:
        >>> SourcePath("./skills/my-skill")
        './skills/my-skill'

        >>> SourcePath("https://github.com/OpenHands/extensions/blob/main/skills/github")
        'https://github.com/OpenHands/extensions/blob/main/skills/github'

        >>> SourcePath("invalid-source")
        ValueError: Invalid source path: 'invalid-source'...
    """

    def __new__(cls, value: str) -> SourcePath:
        """Create a new SourcePath, validating the format."""
        validate_source_path(value)
        return super().__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: type, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Define the Pydantic validation schema for SourcePath."""
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def _validate(cls, value: str) -> SourcePath:
        """Pydantic validator function."""
        return cls(value)

    @property
    def is_local(self) -> bool:
        """Check if this source path is local."""
        return is_local_path(self)

    @property
    def is_github(self) -> bool:
        """Check if this source path is a GitHub URL."""
        return is_github_url(self)

    @property
    def github_components(self) -> GitHubURLComponents | None:
        """Parse GitHub URL components if this is a GitHub URL."""
        return parse_github_url(self)


def get_cache_path_for_github_repo(
    owner: str,
    repo: str,
    cache_dir: Path | None = None,
) -> Path:
    """Get the cache path for a GitHub repository.

    Creates a unique path including github.com to support future gitlab/etc.

    Args:
        owner: Repository owner.
        repo: Repository name.
        cache_dir: Base cache directory. Defaults to ~/.openhands/cache/git.

    Returns:
        Path to the repository cache directory.

    Examples:
        >>> get_cache_path_for_github_repo("OpenHands", "extensions")
        PosixPath('/home/user/.openhands/cache/git/github.com/openhands/extensions')
    """
    base = cache_dir or DEFAULT_CACHE_DIR
    # Use lowercase for consistency across case-sensitive/insensitive filesystems
    return base / "github.com" / owner.lower() / repo.lower()


def get_file_path_from_github_url(
    url: str,
    cache_dir: Path | None = None,
    update: bool = True,
    git_helper: GitHelper | None = None,
) -> Path | None:
    """Resolve a GitHub blob/tree URL to a local file path.

    This function:
    1. Parses the GitHub URL to extract owner, repo, branch, and path
    2. Creates a unique cache path for the repository
    3. Clones or updates the repository
    4. Returns the path to the file/directory within the cache

    Args:
        url: GitHub blob/tree URL like
            https://github.com/{owner}/{repo}/blob/{branch}/{path}
        cache_dir: Base cache directory. Defaults to ~/.openhands/cache/git.
        update: If True and repo exists, fetch and update it.
        git_helper: GitHelper instance for testing.

    Returns:
        Path to the file/directory in the local cache, or None if:
        - URL is not a valid GitHub blob/tree URL
        - Clone/update failed

    Examples:
        >>> get_file_path_from_github_url(
        ...     "https://github.com/OpenHands/extensions/blob/main/skills/github/SKILL.md"
        ... )
        PosixPath('/home/user/.openhands/cache/git/github.com/openhands/extensions/skills/github/SKILL.md')
    """
    components = parse_github_url(url)
    if components is None:
        logger.warning(f"Not a valid GitHub URL: {url}")
        return None

    # Get cache path for this repository
    repo_cache_path = get_cache_path_for_github_repo(
        components.owner, components.repo, cache_dir
    )

    # Build the clone URL
    clone_url = f"https://github.com/{components.owner}/{components.repo}.git"

    # Clone or update the repository
    logger.debug(
        f"Resolving GitHub URL: {url} -> {repo_cache_path} "
        f"(branch: {components.branch})"
    )

    result = try_cached_clone_or_update(
        url=clone_url,
        repo_path=repo_cache_path,
        ref=components.branch,
        update=update,
        git_helper=git_helper,
    )

    if result is None:
        logger.warning(f"Failed to clone/update repository for: {url}")
        return None

    # Return the path to the specific file/directory
    file_path = repo_cache_path / components.path
    return file_path


def resolve_source_path(
    source: str | SourcePath,
    base_path: Path | None = None,
    cache_dir: Path | None = None,
    update: bool = True,
) -> Path | None:
    """Resolve a source path to an absolute local path.

    Handles all source path types:
    - Local paths: resolved relative to base_path
    - File URLs: converted to local path
    - GitHub URLs: cloned/cached and path returned

    Args:
        source: Source path string (local path, file://, or GitHub URL).
        base_path: Base path for resolving relative local paths.
        cache_dir: Cache directory for GitHub repositories.
        update: Whether to update existing cached repos.

    Returns:
        Absolute path to the local file/directory, or None if resolution failed.

    Examples:
        >>> resolve_source_path("./skills/my-skill", base_path=Path("/project"))
        PosixPath('/project/skills/my-skill')

        >>> resolve_source_path("https://github.com/OpenHands/extensions/blob/main/skills/github")
        PosixPath('/home/user/.openhands/cache/git/github.com/openhands/extensions/skills/github')
    """
    source_str = str(source)

    # Handle file:// URLs
    if source_str.startswith("file://"):
        return Path(source_str[7:])  # Remove "file://" prefix

    # Handle GitHub URLs
    if is_github_url(source_str):
        return get_file_path_from_github_url(
            source_str, cache_dir=cache_dir, update=update
        )

    # Handle local paths
    path = Path(source_str).expanduser()

    if path.is_absolute():
        return path

    # Relative path - resolve against base_path
    if base_path is not None:
        return (base_path / path).resolve()

    # No base path, try to resolve against CWD
    return path.resolve()
