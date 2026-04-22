"""Repository cloning and management utilities for OpenHands Cloud workspace.

This module provides utilities for cloning git repositories and loading
skills from them when running inside an OpenHands Cloud sandbox.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    pass


logger = get_logger(__name__)


# Clone timeout in seconds (5 minutes per repo)
CLONE_TIMEOUT = 300


class GitProvider(str, Enum):
    """Supported git hosting providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"


# Mapping of provider to secret name used in sandbox settings
PROVIDER_TOKEN_NAMES: dict[GitProvider, str] = {
    GitProvider.GITHUB: "github_token",
    GitProvider.GITLAB: "gitlab_token",
    GitProvider.BITBUCKET: "bitbucket_token",
}

# Mapping of URL patterns to providers for auto-detection
PROVIDER_URL_PATTERNS: dict[str, GitProvider] = {
    "github.com": GitProvider.GITHUB,
    "gitlab.com": GitProvider.GITLAB,
    "bitbucket.org": GitProvider.BITBUCKET,
}


def _detect_provider_from_url(url: str) -> GitProvider | None:
    """Detect git provider from URL patterns.

    Args:
        url: Repository URL or owner/repo format

    Returns:
        Detected GitProvider or None if not recognized
    """
    url_lower = url.lower()
    for pattern, provider in PROVIDER_URL_PATTERNS.items():
        if pattern in url_lower:
            return provider
    return None


class RepoSource(BaseModel):
    """Repository source specification for cloning.

    Repositories are cloned during automation setup and skills (AGENTS.md,
    .agents/skills/, etc.) are automatically loaded from each cloned repo.

    The provider field specifies which git hosting service the repo belongs to,
    which determines which authentication token to use for cloning. If not
    specified, the provider is auto-detected from the URL (defaulting to GitHub
    for owner/repo format).

    Examples:
        >>> # Simple string URL (defaults to GitHub)
        >>> RepoSource(url="owner/repo")

        >>> # Full URL with ref (provider auto-detected)
        >>> RepoSource(url="https://github.com/owner/repo", ref="main")

        >>> # GitLab repo with explicit provider
        >>> RepoSource(url="owner/repo", provider="gitlab")

        >>> # From dict
        >>> RepoSource.model_validate({"url": "owner/repo", "ref": "v1.0.0"})

        >>> # From string (via model_validator)
        >>> RepoSource.model_validate("owner/repo")
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        ...,
        description=(
            "Repository identifier. Can be 'owner/repo' format "
            "or a full URL (https://github.com/owner/repo, https://gitlab.com/owner/repo)."
        ),
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit SHA to checkout.",
    )
    provider: Literal["github", "gitlab", "bitbucket"] | None = Field(
        default=None,
        description=(
            "Git hosting provider (github, gitlab, bitbucket). "
            "Used to determine which authentication token to use. "
            "If not specified, auto-detected from URL "
            "(defaults to github for owner/repo format)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_string_input(cls, data: Any) -> Any:
        """Allow passing just a URL string instead of full object."""
        if isinstance(data, str):
            return {"url": data}
        return data

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format to provide early feedback."""
        # Allow owner/repo format (e.g., "owner/repo", "my-org/my-repo.git")
        owner_repo_pattern = re.compile(r"^[\w-]+/[\w.-]+$")
        if owner_repo_pattern.match(v):
            return v
        # Allow full git URLs
        if v.startswith(("http://", "https://", "git@")):
            return v
        raise ValueError(
            "URL must be 'owner/repo' format or a valid git URL "
            "(https://, http://, or git@)"
        )

    def get_provider(self) -> GitProvider:
        """Get the git provider for this repo.

        Returns the explicitly set provider, or auto-detects from URL.
        Defaults to GitHub for owner/repo format without explicit provider.
        """
        if self.provider:
            return GitProvider(self.provider)

        detected = _detect_provider_from_url(self.url)
        if detected:
            return detected

        # Default to GitHub for owner/repo format
        return GitProvider.GITHUB

    def get_token_name(self) -> str:
        """Get the secret name for this repo's authentication token."""
        return PROVIDER_TOKEN_NAMES[self.get_provider()]


@dataclass
class RepoMapping:
    """Mapping information for a cloned repository."""

    url: str
    dir_name: str
    local_path: str
    ref: str | None = None


@dataclass
class CloneResult:
    """Result of repository cloning operations."""

    success_count: int
    failed_repos: list[str]
    repo_mappings: dict[str, RepoMapping] = field(default_factory=dict)


def _is_commit_sha(ref: str | None) -> bool:
    """Check if ref looks like a git commit SHA."""
    if not ref:
        return False
    return bool(re.match(r"^[0-9a-f]{7,40}$", ref, re.IGNORECASE))


def _extract_repo_name(url: str) -> str:
    """Extract repository name from URL for use as directory name.

    Examples:
        >>> _extract_repo_name("owner/repo")
        'repo'
        >>> _extract_repo_name("https://github.com/owner/repo.git")
        'repo'
        >>> _extract_repo_name("git@github.com:owner/repo.git")
        'repo'
    """
    # Remove trailing .git
    url = re.sub(r"\.git$", "", url)

    # Handle git@host:owner/repo format
    if url.startswith("git@"):
        url = url.split(":")[-1]

    # Handle https://host/owner/repo format
    if "://" in url:
        url = url.split("://")[-1]

    # Get the last path component (repo name)
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else "repo"


def _sanitize_dir_name(name: str) -> str:
    """Sanitize a string for use as a directory name.

    Replaces invalid characters with underscores and ensures the name is safe.
    """
    # Replace characters that are problematic in file paths
    sanitized = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name)
    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip(". ")
    # Ensure non-empty
    return sanitized if sanitized else "repo"


def _get_unique_dir_name(base_name: str, existing_dirs: set[str]) -> str:
    """Get a unique directory name, appending _N if needed.

    Args:
        base_name: The desired directory name
        existing_dirs: Set of already-used directory names

    Returns:
        A unique directory name (base_name or base_name_1, base_name_2, etc.)
    """
    if base_name not in existing_dirs:
        return base_name

    # Find next available suffix
    counter = 1
    while f"{base_name}_{counter}" in existing_dirs:
        counter += 1
    return f"{base_name}_{counter}"


def _build_clone_url(url: str, provider: GitProvider, token: str | None) -> str:
    """Build authenticated clone URL based on the repository URL and provider.

    Args:
        url: Repository URL or owner/repo format
        provider: Git hosting provider
        token: Authentication token for the provider (may be None)

    Returns:
        Clone URL with authentication if token is available
    """
    # Handle owner/repo format - construct full URL based on provider
    if "://" not in url and "/" in url and not url.startswith("git@"):
        if provider == GitProvider.GITHUB:
            base_url = "github.com"
            if token:
                return f"https://{token}@{base_url}/{url}.git"
            return f"https://{base_url}/{url}.git"
        elif provider == GitProvider.GITLAB:
            base_url = "gitlab.com"
            if token:
                return f"https://oauth2:{token}@{base_url}/{url}.git"
            return f"https://{base_url}/{url}.git"
        elif provider == GitProvider.BITBUCKET:
            base_url = "bitbucket.org"
            if token:
                return f"https://x-token-auth:{token}@{base_url}/{url}.git"
            return f"https://{base_url}/{url}.git"

    # Handle full URLs - inject authentication
    if not token:
        return url

    if provider == GitProvider.GITHUB and "github.com" in url:
        return url.replace("https://github.com", f"https://{token}@github.com")
    elif provider == GitProvider.GITLAB and "gitlab.com" in url:
        return url.replace("https://gitlab.com", f"https://oauth2:{token}@gitlab.com")
    elif provider == GitProvider.BITBUCKET and "bitbucket.org" in url:
        return url.replace(
            "https://bitbucket.org", f"https://x-token-auth:{token}@bitbucket.org"
        )

    # Return as-is for other URLs or unrecognized patterns
    return url


def _mask_url(url: str) -> str:
    """Remove credentials from URL for display."""
    if "://" not in url:
        return url
    return url.split("://")[0] + "://" + url.split("://")[-1].split("@")[-1]


def _mask_token(text: str, token: str | None) -> str:
    """Mask token in text for safe logging."""
    if token:
        text = text.replace(token, "***")
    return text


TokenFetcher = Any  # Callable[[str], str | None] - fetches token by name


def clone_repos(
    repos: list[RepoSource],
    target_dir: Path,
    token_fetcher: TokenFetcher | None = None,
) -> CloneResult:
    """Clone repositories to the target directory.

    Clones repos to meaningful directory names (e.g., 'openhands-cli' instead of
    'repo_0'). Uses the provider specified in each RepoSource to determine which
    authentication token to use.

    Args:
        repos: List of RepoSource configurations (each specifies provider)
        target_dir: Directory to clone repositories into
        token_fetcher: Callable that takes a token name (e.g., 'github_token')
            and returns the token value, or None if not available

    Returns:
        CloneResult with success count, failed repos, and repo mapping
    """
    if not repos:
        logger.info("[clone] No repositories to clone")
        return CloneResult(success_count=0, failed_repos=[], repo_mappings={})

    logger.info(f"[clone] Cloning {len(repos)} repository(ies)...")

    # Create target directory
    target_dir.mkdir(parents=True, exist_ok=True)

    # Cache fetched tokens to avoid repeated API calls
    token_cache: dict[str, str | None] = {}

    def get_token(token_name: str) -> str | None:
        """Get token from cache or fetch it."""
        if token_name not in token_cache:
            if token_fetcher:
                token_cache[token_name] = token_fetcher(token_name)
            else:
                token_cache[token_name] = None
        return token_cache[token_name]

    # Track used directory names to handle collisions
    used_dir_names: set[str] = set()
    failed_repos: list[str] = []
    repo_mappings: dict[str, RepoMapping] = {}
    success_count = 0

    for repo in repos:
        url = repo.url
        ref = repo.ref

        if not url:
            logger.warning("[clone] Skipping repo: no URL specified")
            continue

        # Get provider and token for this specific repo
        provider = repo.get_provider()
        token_name = repo.get_token_name()
        token = get_token(token_name)

        logger.debug(f"[clone] Repo {url} using provider {provider.value}")

        # Determine directory name from repo URL
        raw_name = _extract_repo_name(url)
        safe_name = _sanitize_dir_name(raw_name)
        dir_name = _get_unique_dir_name(safe_name, used_dir_names)
        used_dir_names.add(dir_name)

        dest = target_dir / dir_name
        clone_url = _build_clone_url(url, provider, token)
        display_url = _mask_url(url)

        # Build git clone command
        # Note: --depth 1 with --branch only works for branches/tags, not SHAs.
        # For SHA refs, we do a full clone then checkout the specific commit.
        if _is_commit_sha(ref):
            # Full clone for SHA refs (shallow clone can't fetch arbitrary commits)
            cmd = ["git", "clone", clone_url, str(dest)]
            needs_checkout = True
        else:
            # Shallow clone for branches/tags
            cmd = ["git", "clone", "--depth", "1"]
            if ref:
                cmd.extend(["--branch", ref])
            cmd.extend([clone_url, str(dest)])
            needs_checkout = False

        logger.info(f"[clone] Cloning {display_url} ({provider.value}) -> {dest.name}/")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT
            )
            if result.returncode != 0:
                error_msg = _mask_token(result.stderr, token)
                logger.warning(f"[clone] Failed to clone {display_url}: {error_msg}")
                failed_repos.append(display_url)
                continue

            # Checkout specific SHA if needed
            if needs_checkout and ref:
                checkout_result = subprocess.run(
                    ["git", "-C", str(dest), "checkout", ref],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if checkout_result.returncode != 0:
                    logger.warning(
                        f"[clone] Failed to checkout {ref}: {checkout_result.stderr}"
                    )
                    failed_repos.append(display_url)
                    continue

            logger.info(f"[clone] Successfully cloned {display_url} -> {dir_name}/")
            success_count += 1

            # Record mapping
            repo_mappings[url] = RepoMapping(
                url=url,
                dir_name=dir_name,
                local_path=str(dest),
                ref=ref,
            )

        except subprocess.TimeoutExpired:
            logger.warning(f"[clone] Clone timed out for {display_url}")
            failed_repos.append(display_url)
            continue

    logger.info(f"[clone] Cloned {success_count}/{len(repos)} repositories")
    if failed_repos:
        logger.warning(f"[clone] FAILED repos: {', '.join(failed_repos)}")

    return CloneResult(
        success_count=success_count,
        failed_repos=failed_repos,
        repo_mappings=repo_mappings,
    )


def get_repos_context(repo_mappings: dict[str, RepoMapping]) -> str:
    """Generate a context string describing cloned repositories for the agent.

    Args:
        repo_mappings: Dictionary mapping URLs to RepoMapping objects

    Returns:
        Markdown-formatted string with repository mapping, or empty string if no repos.
    """
    if not repo_mappings:
        return ""

    lines = [
        "## Cloned Repositories",
        "",
        "The following repositories have been cloned to your workspace:",
        "",
    ]

    for url, mapping in repo_mappings.items():
        ref_str = f" (ref: {mapping.ref})" if mapping.ref else ""
        lines.append(f"- `{url}`{ref_str} → `{mapping.local_path}/`")

    lines.append("")
    return "\n".join(lines)
