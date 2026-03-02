"""Utility functions for skill loading and management."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp.mcp_config import MCPConfig

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.git.cached_repo import try_cached_clone_or_update
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.context.skills.skill import Skill, SkillResources

logger = get_logger(__name__)

# Standard resource directory names per AgentSkills spec
RESOURCE_DIRECTORIES = ("scripts", "references", "assets")

# Regex pattern for valid AgentSkills names
# - 1-64 characters
# - Lowercase alphanumeric + hyphens only (a-z, 0-9, -)
# - Must not start or end with hyphen
# - Must not contain consecutive hyphens (--)
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def find_skill_md(skill_dir: Path) -> Path | None:
    """Find SKILL.md file in a directory (case-insensitive).

    Args:
        skill_dir: Path to the skill directory to search.

    Returns:
        Path to SKILL.md if found, None otherwise.
    """
    if not skill_dir.is_dir():
        return None
    for item in skill_dir.iterdir():
        if item.is_file() and item.name.lower() == "skill.md":
            return item
    return None


def find_mcp_config(skill_dir: Path) -> Path | None:
    """Find .mcp.json file in a skill directory.

    Args:
        skill_dir: Path to the skill directory to search.

    Returns:
        Path to .mcp.json if found, None otherwise.
    """
    if not skill_dir.is_dir():
        return None
    mcp_json = skill_dir / ".mcp.json"
    if mcp_json.exists() and mcp_json.is_file():
        return mcp_json
    return None


def expand_mcp_variables(
    config: dict,
    variables: dict[str, str],
) -> dict:
    """Expand variables in MCP configuration.

    Supports variable expansion similar to Claude Code:
    - ${VAR} - Environment variables or provided variables
    - ${VAR:-default} - With default value

    Args:
        config: MCP configuration dictionary.
        variables: Dictionary of variable names to values.

    Returns:
        Configuration with variables expanded.
    """
    # Convert to JSON string for easy replacement
    config_str = json.dumps(config)

    # Pattern for ${VAR} or ${VAR:-default}
    var_pattern = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")

    def replace_var(match: re.Match) -> str:
        var_name = match.group(1)
        default_value = match.group(2)

        # Check provided variables first, then environment
        if var_name in variables:
            return variables[var_name]
        if var_name in os.environ:
            return os.environ[var_name]
        if default_value is not None:
            return default_value
        # Return original if not found
        return match.group(0)

    config_str = var_pattern.sub(replace_var, config_str)
    return json.loads(config_str)


def load_mcp_config(
    mcp_json_path: Path,
    skill_root: Path | None = None,
) -> dict:
    """Load and parse .mcp.json with variable expansion.

    Args:
        mcp_json_path: Path to the .mcp.json file.
        skill_root: Root directory of the skill (for ${SKILL_ROOT} expansion).

    Returns:
        Parsed MCP configuration dictionary.

    Raises:
        SkillValidationError: If the file cannot be parsed or is invalid.
    """
    try:
        with open(mcp_json_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise SkillValidationError(f"Invalid JSON in {mcp_json_path}: {e}") from e
    except OSError as e:
        raise SkillValidationError(f"Cannot read {mcp_json_path}: {e}") from e

    if not isinstance(config, dict):
        raise SkillValidationError(
            f"Invalid .mcp.json format: expected object, got {type(config).__name__}"
        )

    # Prepare variables for expansion
    variables: dict[str, str] = {}
    if skill_root:
        variables["SKILL_ROOT"] = str(skill_root)

    # Expand variables
    config = expand_mcp_variables(config, variables)

    # Validate using MCPConfig
    try:
        MCPConfig.model_validate(config)
    except Exception as e:
        raise SkillValidationError(f"Invalid MCP configuration: {e}") from e

    return config


def validate_skill_name(name: str, directory_name: str | None = None) -> list[str]:
    """Validate skill name according to AgentSkills spec.

    Args:
        name: The skill name to validate.
        directory_name: Optional directory name to check for match.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors = []

    if not name:
        errors.append("Name cannot be empty")
        return errors

    if len(name) > 64:
        errors.append(f"Name exceeds 64 characters: {len(name)}")

    if not SKILL_NAME_PATTERN.match(name):
        errors.append(
            "Name must be lowercase alphanumeric with single hyphens "
            "(e.g., 'my-skill', 'pdf-tools')"
        )

    if directory_name and name != directory_name:
        errors.append(f"Name '{name}' does not match directory '{directory_name}'")

    return errors


def find_third_party_files(
    repo_root: Path, third_party_skill_names: dict[str, str]
) -> list[Path]:
    """Find third-party skill files in the repository root.

    Searches for files like .cursorrules, AGENTS.md, CLAUDE.md, etc.
    with case-insensitive matching.

    Args:
        repo_root: Path to the repository root directory.
        third_party_skill_names: Mapping of lowercase filenames to skill names.

    Returns:
        List of paths to third-party skill files found.
    """
    if not repo_root.exists():
        return []

    # Build a set of target filenames (lowercase) for case-insensitive matching
    target_names = {name.lower() for name in third_party_skill_names}

    files: list[Path] = []
    seen_names: set[str] = set()
    for item in repo_root.iterdir():
        if item.is_file() and item.name.lower() in target_names:
            # Avoid duplicates (e.g., AGENTS.md and agents.md in same dir)
            name_lower = item.name.lower()
            if name_lower in seen_names:
                logger.warning(
                    f"Duplicate third-party skill file ignored: {item} "
                    f"(already found a file with name '{name_lower}')"
                )
            else:
                files.append(item)
                seen_names.add(name_lower)
    return files


def find_skill_md_directories(skill_dir: Path) -> list[Path]:
    """Find AgentSkills-style directories containing SKILL.md files.

    Args:
        skill_dir: Path to the skills directory.

    Returns:
        List of paths to SKILL.md files.
    """
    results: list[Path] = []
    if not skill_dir.exists():
        return results
    for subdir in skill_dir.iterdir():
        if subdir.is_dir():
            skill_md = find_skill_md(subdir)
            if skill_md:
                results.append(skill_md)
    return results


def find_regular_md_files(skill_dir: Path, exclude_dirs: set[Path]) -> list[Path]:
    """Find regular .md skill files, excluding SKILL.md and files in excluded dirs.

    Args:
        skill_dir: Path to the skills directory.
        exclude_dirs: Set of directories to exclude (e.g., SKILL.md directories).

    Returns:
        List of paths to regular .md skill files.
    """
    files: list[Path] = []
    if not skill_dir.exists():
        return files
    for f in skill_dir.rglob("*.md"):
        is_readme = f.name == "README.md"
        is_skill_md = f.name.lower() == "skill.md"
        is_in_excluded_dir = any(f.is_relative_to(d) for d in exclude_dirs)
        if not is_readme and not is_skill_md and not is_in_excluded_dir:
            files.append(f)
    return files


def load_and_categorize(
    path: Path,
    skill_base_dir: Path,
    repo_skills: dict[str, Skill],
    knowledge_skills: dict[str, Skill],
    agent_skills: dict[str, Skill],
) -> None:
    """Load a skill and categorize it.

    Categorizes into repo_skills, knowledge_skills, or agent_skills.

    Args:
        path: Path to the skill file.
        skill_base_dir: Base directory for skills (used to derive relative names).
        repo_skills: Dictionary for skills with trigger=None (permanent context).
        knowledge_skills: Dictionary for skills with triggers (progressive).
        agent_skills: Dictionary for AgentSkills standard SKILL.md files.
    """
    # Import here to avoid circular dependency
    from openhands.sdk.context.skills.skill import Skill

    skill = Skill.load(path, skill_base_dir)

    # AgentSkills (SKILL.md directories) are a separate category from OpenHands skills.
    # They follow the AgentSkills standard and should be handled differently.
    is_skill_md = path.name.lower() == "skill.md"
    if is_skill_md:
        agent_skills[skill.name] = skill
    elif skill.trigger is None:
        repo_skills[skill.name] = skill
    else:
        knowledge_skills[skill.name] = skill


def get_skills_cache_dir() -> Path:
    """Get the local cache directory for public skills repository.

    Returns:
        Path to the skills cache directory (~/.openhands/cache/skills).
    """
    cache_dir = Path.home() / ".openhands" / "cache" / "skills"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def update_skills_repository(
    repo_url: str,
    branch: str,
    cache_dir: Path,
    cache_name: str | None = None,
) -> Path | None:
    """Clone or update the local skills repository.

    Uses the shared git caching infrastructure from openhands.sdk.git.cached_repo.
    When updating, performs: fetch -> checkout ref -> reset --hard to origin/ref.

    Args:
        repo_url: URL of the skills repository.
        branch: Branch name to checkout and track.
        cache_dir: Directory where the repository should be cached.
        cache_name: Optional name for the cache directory. If not provided,
            derives from the repo URL (e.g., 'owner-repo' from 'github.com/owner/repo').

    Returns:
        Path to the local repository if successful, None otherwise.
    """
    if cache_name is None:
        # Derive cache name from repo URL
        cache_name = get_cache_name_from_url(repo_url)
    repo_path = cache_dir / cache_name
    return try_cached_clone_or_update(repo_url, repo_path, ref=branch, update=True)


def get_cache_name_from_url(repo_url: str) -> str:
    """Derive a cache directory name from a repository URL.

    Args:
        repo_url: Repository URL (e.g., 'https://github.com/owner/repo').

    Returns:
        Cache directory name (e.g., 'owner-repo').
    """
    # Remove protocol and trailing slashes
    url = repo_url.rstrip("/")
    if "://" in url:
        url = url.split("://", 1)[1]

    # Remove .git suffix
    if url.endswith(".git"):
        url = url[:-4]

    # Extract owner/repo from various formats
    # github.com/owner/repo -> owner-repo
    # gitlab.com/owner/repo -> owner-repo
    parts = url.split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}-{parts[-1]}"
    return parts[-1] if parts else "public-skills"


def parse_marketplace_path(
    marketplace_path: str,
) -> tuple[str | None, str, str]:
    """Parse a marketplace path into repo owner/name, branch, and file path.

    Supports formats:
    - "owner/repo:path/to/marketplace.json" - GitHub repo with path
    - "owner/repo:path/to/marketplace.json@branch" - With specific branch
    - "path/to/marketplace.json" - Default repo (OpenHands/extensions)

    Args:
        marketplace_path: Marketplace path in one of the supported formats.

    Returns:
        Tuple of (repo_spec, branch, file_path) where:
        - repo_spec: "owner/repo" or None for default repo
        - branch: Branch name (defaults to "main")
        - file_path: Path to marketplace file within repo
    """
    # Default branch
    branch = "main"

    # Check for @branch suffix
    if "@" in marketplace_path:
        path_part, branch = marketplace_path.rsplit("@", 1)
        marketplace_path = path_part

    # Check for owner/repo:path format
    if ":" in marketplace_path:
        repo_spec, file_path = marketplace_path.split(":", 1)
        # Validate it looks like owner/repo
        if "/" in repo_spec and not repo_spec.startswith("/"):
            return (repo_spec, branch, file_path)

    # No repo spec - use default repo
    return (None, branch, marketplace_path)


def discover_skill_resources(skill_dir: Path) -> SkillResources:
    """Discover resource directories in a skill directory.

    Scans for standard AgentSkills resource directories:
    - scripts/: Executable scripts
    - references/: Reference documentation
    - assets/: Static assets

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        SkillResources with lists of files in each resource directory.
    """
    # Import here to avoid circular dependency
    from openhands.sdk.context.skills.skill import SkillResources

    resources = SkillResources(skill_root=str(skill_dir.resolve()))

    for resource_type in RESOURCE_DIRECTORIES:
        resource_dir = skill_dir / resource_type
        if resource_dir.is_dir():
            files = _list_resource_files(resource_dir, resource_type)
            setattr(resources, resource_type, files)

    return resources


def _list_resource_files(
    resource_dir: Path,
    resource_type: str,
) -> list[str]:
    """List files in a resource directory.

    Args:
        resource_dir: Path to the resource directory.
        resource_type: Type of resource (scripts, references, assets).

    Returns:
        List of relative file paths within the resource directory.
    """
    files: list[str] = []
    try:
        for item in resource_dir.rglob("*"):
            if item.is_file():
                # Store relative path from resource directory
                rel_path = item.relative_to(resource_dir)
                files.append(str(rel_path))
    except OSError as e:
        logger.warning(f"Error listing {resource_type} directory: {e}")
    return sorted(files)
