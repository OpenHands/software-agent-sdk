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


def _is_short_url_format(url: str) -> bool:
    """Check if URL is the short 'owner/repo' format (no protocol)."""
    return "://" not in url and not url.startswith("git@")


class RepoSource(BaseModel):
    """Repository source specification for cloning.

    Repositories are cloned during automation setup and skills (AGENTS.md,
    .agents/skills/, etc.) are automatically loaded from each cloned repo.

    The provider field specifies which git hosting service the repo belongs to,
    which determines which authentication token to use for cloning.

    For full URLs (https://github.com/...), the provider is auto-detected.
    For short format (owner/repo), the provider field is required.

    Examples:
        >>> # Full URL - provider auto-detected
        >>> RepoSource(url="https://github.com/owner/repo")
        >>> RepoSource(url="https://gitlab.com/owner/repo", ref="main")

        >>> # Short format - provider required
        >>> RepoSource(url="owner/repo", provider="github")
        >>> RepoSource(url="owner/repo", provider="gitlab", ref="v1.0.0")
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        ...,
        description=(
            "Repository URL. Can be a full URL (https://github.com/owner/repo) "
            "or short format (owner/repo). Short format requires 'provider' field."
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
            "Required for short URL format (owner/repo). "
            "Auto-detected for full URLs."
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
        """Validate URL format."""
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

    @model_validator(mode="after")
    def validate_provider_required_for_short_urls(self) -> RepoSource:
        """Require explicit provider for ambiguous short URL format."""
        if not _is_short_url_format(self.url):
            # Full URL - provider can be auto-detected
            return self

        # Short format - check if provider is specified or detectable
        detected = _detect_provider_from_url(self.url)
        if not detected and not self.provider:
            raise ValueError(
                f"Short URL format '{self.url}' requires explicit 'provider' field. "
                "Use: {\"url\": \"owner/repo\", \"provider\": \"github\"} "
                "or provide a full URL like https://github.com/owner/repo"
            )
        return self

    def get_provider(self) -> GitProvider:
        """Get the git provider for this repo."""
        if self.provider:
            return GitProvider(self.provider)

        detected = _detect_provider_from_url(self.url)
        if detected:
            return detected

        # This shouldn't happen if validation passed
        raise ValueError(f"Cannot determine provider for URL: {self.url}")

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


# Provider configurations: (base_url, token_format)
# token_format uses {token} placeholder
_PROVIDER_CONFIG: dict[GitProvider, tuple[str, str]] = {
    GitProvider.GITHUB: ("github.com", "{token}@"),
    GitProvider.GITLAB: ("gitlab.com", "oauth2:{token}@"),
    GitProvider.BITBUCKET: ("bitbucket.org", "x-token-auth:{token}@"),
}


def _build_clone_url(url: str, provider: GitProvider, token: str | None) -> str:
    """Build authenticated clone URL based on the repository URL and provider."""
    config = _PROVIDER_CONFIG.get(provider)
    if not config:
        return url

    base_url, token_format = config
    auth_prefix = token_format.format(token=token) if token else ""

    # Handle owner/repo format - construct full URL
    is_short_format = "://" not in url and "/" in url and not url.startswith("git@")
    if is_short_format:
        return f"https://{auth_prefix}{base_url}/{url}.git"

    # Handle full URLs - inject authentication
    if token and base_url in url:
        return url.replace(f"https://{base_url}", f"https://{auth_prefix}{base_url}")

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


def _clone_single_repo(
    repo: RepoSource,
    dest: Path,
    token: str | None,
) -> bool:
    """Clone a single repository. Returns True on success."""
    url, ref = repo.url, repo.ref
    provider = repo.get_provider()
    clone_url = _build_clone_url(url, provider, token)
    display_url = _mask_url(url)

    # Build git clone command
    # SHA refs need full clone + checkout; branches/tags can use shallow clone
    is_sha = _is_commit_sha(ref)
    if is_sha:
        cmd = ["git", "clone", clone_url, str(dest)]
    else:
        cmd = ["git", "clone", "--depth", "1"]
        if ref:
            cmd.extend(["--branch", ref])
        cmd.extend([clone_url, str(dest)])

    logger.info(f"[clone] Cloning {display_url} ({provider.value}) -> {dest.name}/")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT
        )
        if result.returncode != 0:
            logger.warning(f"[clone] Failed: {_mask_token(result.stderr, token)}")
            return False

        # Checkout specific SHA if needed
        if is_sha and ref:
            checkout = subprocess.run(
                ["git", "-C", str(dest), "checkout", ref],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if checkout.returncode != 0:
                logger.warning(f"[clone] Failed to checkout {ref}: {checkout.stderr}")
                return False

        logger.info(f"[clone] Success: {display_url} -> {dest.name}/")
        return True

    except subprocess.TimeoutExpired:
        logger.warning(f"[clone] Timed out: {display_url}")
        return False


def clone_repos(
    repos: list[RepoSource],
    target_dir: Path,
    token_fetcher: TokenFetcher | None = None,
) -> CloneResult:
    """Clone repositories to the target directory.

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
    target_dir.mkdir(parents=True, exist_ok=True)

    # Cache tokens to avoid repeated API calls
    token_cache: dict[str, str | None] = {}

    def get_token(name: str) -> str | None:
        if name not in token_cache:
            token_cache[name] = token_fetcher(name) if token_fetcher else None
        return token_cache[name]

    used_dirs: set[str] = set()
    failed: list[str] = []
    mappings: dict[str, RepoMapping] = {}

    for repo in repos:
        if not repo.url:
            continue

        # Get unique directory name
        dir_name = _get_unique_dir_name(
            _sanitize_dir_name(_extract_repo_name(repo.url)), used_dirs
        )
        used_dirs.add(dir_name)
        dest = target_dir / dir_name

        # Clone with provider-specific token
        token = get_token(repo.get_token_name())
        if _clone_single_repo(repo, dest, token):
            mappings[repo.url] = RepoMapping(
                url=repo.url,
                dir_name=dir_name,
                local_path=str(dest),
                ref=repo.ref,
            )
        else:
            failed.append(_mask_url(repo.url))

    logger.info(f"[clone] Cloned {len(mappings)}/{len(repos)} repositories")
    if failed:
        logger.warning(f"[clone] Failed: {', '.join(failed)}")

    return CloneResult(
        success_count=len(mappings),
        failed_repos=failed,
        repo_mappings=mappings,
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
