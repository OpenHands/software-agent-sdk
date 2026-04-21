"""Repository cloning and management utilities for OpenHands Cloud workspace.

This module provides utilities for cloning git repositories and loading
skills from them when running inside an OpenHands Cloud sandbox.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    pass


logger = get_logger(__name__)


# Clone timeout in seconds (5 minutes per repo)
CLONE_TIMEOUT = 300


class RepoSource(BaseModel):
    """Repository source specification for cloning.

    Repositories are cloned during automation setup and skills (AGENTS.md,
    .agents/skills/, etc.) are automatically loaded from each cloned repo.

    Examples:
        >>> # Simple string URL
        >>> RepoSource(url="owner/repo")

        >>> # Full URL with ref
        >>> RepoSource(url="https://github.com/owner/repo", ref="main")

        >>> # From dict
        >>> RepoSource.model_validate({"url": "owner/repo", "ref": "v1.0.0"})

        >>> # From string (via model_validator)
        >>> RepoSource.model_validate("owner/repo")
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        ...,
        description=(
            "Repository identifier. Can be 'owner/repo' format (assumes GitHub) "
            "or a full URL (https://github.com/owner/repo, https://gitlab.com/owner/repo)."
        ),
    )
    ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit SHA to checkout.",
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


def _build_clone_url(
    url: str, github_token: str | None, gitlab_token: str | None
) -> str:
    """Build authenticated clone URL based on the repository URL."""
    # Handle owner/repo format (assume GitHub)
    if "://" not in url and "/" in url and not url.startswith("git@"):
        if github_token:
            return f"https://{github_token}@github.com/{url}.git"
        return f"https://github.com/{url}.git"

    # Handle full URLs
    if "github.com" in url:
        if github_token:
            return url.replace(
                "https://github.com", f"https://{github_token}@github.com"
            )
        return url
    elif "gitlab.com" in url:
        if gitlab_token:
            return url.replace(
                "https://gitlab.com", f"https://oauth2:{gitlab_token}@gitlab.com"
            )
        return url

    # Return as-is for other URLs
    return url


def _mask_url(url: str) -> str:
    """Remove credentials from URL for display."""
    if "://" not in url:
        return url
    return url.split("://")[0] + "://" + url.split("://")[-1].split("@")[-1]


def _mask_tokens(text: str, github_token: str | None, gitlab_token: str | None) -> str:
    """Mask tokens in text for safe logging."""
    if github_token:
        text = text.replace(github_token, "***")
    if gitlab_token:
        text = text.replace(gitlab_token, "***")
    return text


def clone_repos(
    repos: list[RepoSource],
    target_dir: Path,
    github_token: str | None = None,
    gitlab_token: str | None = None,
    mapping_file: Path | None = None,
) -> CloneResult:
    """Clone repositories to the target directory.

    Clones repos to meaningful directory names (e.g., 'openhands-cli' instead of
    'repo_0'). Optionally writes a repos_mapping.json file with the URL → local
    path mapping.

    Args:
        repos: List of RepoSource configurations
        target_dir: Directory to clone repositories into
        github_token: Optional GitHub token for authentication
        gitlab_token: Optional GitLab token for authentication
        mapping_file: Optional path to write repo mapping JSON file

    Returns:
        CloneResult with success count, failed repos, and repo mapping
    """
    if not repos:
        logger.info("[clone] No repositories to clone")
        return CloneResult(success_count=0, failed_repos=[], repo_mappings={})

    logger.info(f"[clone] Cloning {len(repos)} repository(ies)...")

    # Create target directory
    target_dir.mkdir(parents=True, exist_ok=True)

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

        # Determine directory name from repo URL
        raw_name = _extract_repo_name(url)
        safe_name = _sanitize_dir_name(raw_name)
        dir_name = _get_unique_dir_name(safe_name, used_dir_names)
        used_dir_names.add(dir_name)

        dest = target_dir / dir_name
        clone_url = _build_clone_url(url, github_token, gitlab_token)
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

        logger.info(f"[clone] Cloning {display_url} -> {dest.name}/")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT
            )
            if result.returncode != 0:
                error_msg = _mask_tokens(result.stderr, github_token, gitlab_token)
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

    # Write mapping file if requested
    if mapping_file and repo_mappings:
        mapping_data = {
            url: {
                "dir_name": m.dir_name,
                "local_path": m.local_path,
                "ref": m.ref,
            }
            for url, m in repo_mappings.items()
        }
        with open(mapping_file, "w") as f:
            json.dump(mapping_data, f, indent=2)
        logger.info(f"[clone] Wrote repository mapping to {mapping_file.name}")

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
