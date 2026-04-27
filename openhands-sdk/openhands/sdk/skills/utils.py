"""Utility functions for skill loading and management."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp.mcp_config import MCPConfig

from openhands.sdk.git.cached_repo import try_cached_clone_or_update
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.exceptions import SkillValidationError


if TYPE_CHECKING:
    from openhands.sdk.skills.skill import Skill, SkillResources

# Type alias for secret lookup functions
SecretLookup = Callable[[str], str | None]

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


def _serialize_for_json(obj: object) -> object:
    """Recursively convert Pydantic models to dicts for JSON serialization.

    This handles the case where MCP config contains Pydantic model objects
    (RemoteMCPServer, StdioMCPServer) instead of plain dicts.
    """
    # Check for Pydantic v2 model_dump method
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj


def expand_mcp_variables(
    config: dict,
    variables: dict[str, str],
    get_secret: SecretLookup | None = None,
    *,  # keyword-only after this (PEP 3102)
    expand_defaults: bool = True,
) -> dict:
    """Expand variables in MCP configuration.

    Supports variable expansion similar to Claude Code:
    - ${VAR} - Environment variables, provided variables, or secrets
    - ${VAR:-default} - With default value

    Resolution order:
    1. Provided variables (e.g., SKILL_ROOT)
    2. Secrets (via get_secret callback, if provided)
    3. Environment variables
    4. Default value (if specified and expand_defaults=True)

    Args:
        config: MCP configuration dictionary. May contain Pydantic model objects
            (e.g., RemoteMCPServer, StdioMCPServer) which will be converted to
            dicts before JSON serialization.
        variables: Dictionary of variable names to values (e.g., SKILL_ROOT).
        get_secret: Callback to look up a secret by name. We use a callback
            rather than a dict to avoid extracting all secrets into plain text.
            Pass `secret_registry.get_secret_value` or `{"K": "V"}.get` for tests.
        expand_defaults: If True, apply default values for unresolved variables.
            If False, preserve ${VAR:-default} as-is for later expansion.
            This allows deferred expansion when secrets are not yet available.

    Returns:
        Configuration with variables expanded.
    """
    # Convert Pydantic models to plain containers before variable expansion.
    serializable_config = _serialize_for_json(config)

    # Pattern for ${VAR} or ${VAR:-default}
    var_pattern = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")

    def replace_var(match: re.Match) -> str:
        var_name = match.group(1)
        default_value = match.group(2)

        # Check provided variables first, then secrets, then environment
        if var_name in variables:
            return variables[var_name]
        if get_secret is not None:
            secret_value = get_secret(var_name)
            if secret_value is not None:
                return secret_value
        if var_name in os.environ:
            return os.environ[var_name]
        # Apply default only if expand_defaults is True
        if expand_defaults and default_value is not None:
            return default_value
        # Return original if not found (preserves placeholder for later expansion)
        return match.group(0)

    def expand_value(value: object) -> object:
        if isinstance(value, str):
            return var_pattern.sub(replace_var, value)
        if isinstance(value, dict):
            return {key: expand_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand_value(item) for item in value]
        return value

    expanded_config = expand_value(serializable_config)
    if not isinstance(expanded_config, dict):
        raise TypeError("expanded MCP config must be a dictionary")
    return expanded_config


def load_mcp_config(
    mcp_json_path: Path,
    skill_root: Path | None = None,
    get_secret: SecretLookup | None = None,
    *,  # keyword-only after this (PEP 3102)
    expand_defaults: bool = True,
) -> dict:
    """Load and parse .mcp.json with variable expansion.

    Args:
        mcp_json_path: Path to the .mcp.json file.
        skill_root: Root directory of the skill (for ${SKILL_ROOT} expansion).
        get_secret: Optional callback to look up per-conversation secrets.
            See expand_mcp_variables() for details on why this is a callback.
        expand_defaults: If True, apply default values for unresolved variables.
            If False, preserve ${VAR:-default} as-is for later expansion.
            Use False during plugin loading to defer until secrets are available.

    Returns:
        Parsed MCP configuration dictionary.

    Raises:
        SkillValidationError: If the file cannot be parsed or is invalid.
    """
    try:
        with open(mcp_json_path, encoding="utf-8") as f:
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

    # Expand variables (includes secrets if provided)
    config = expand_mcp_variables(
        config, variables, get_secret=get_secret, expand_defaults=expand_defaults
    )

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

    Resolves symlinks so that e.g. ``CLAUDE.md -> AGENTS.md`` is detected
    as a duplicate and only the canonical (non-symlink) file is returned.

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
    seen_real_paths: set[Path] = set()
    for item in repo_root.iterdir():
        if item.is_file() and item.name.lower() in target_names:
            # Avoid duplicates (e.g., AGENTS.md and agents.md in same dir)
            name_lower = item.name.lower()
            if name_lower in seen_names:
                logger.warning(
                    f"Duplicate third-party skill file ignored: {item} "
                    f"(already found a file with name '{name_lower}')"
                )
                continue

            # Resolve symlinks to detect e.g. CLAUDE.md -> AGENTS.md
            real_path = item.resolve()
            if real_path in seen_real_paths:
                logger.debug(
                    f"Symlinked third-party skill file ignored: {item} "
                    f"(resolves to already-loaded {real_path})"
                )
                continue

            files.append(item)
            seen_names.add(name_lower)
            seen_real_paths.add(real_path)
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
    from openhands.sdk.skills.skill import Skill

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
) -> Path | None:
    """Clone or update the local skills repository.

    Uses the shared git caching infrastructure from openhands.sdk.git.cached_repo.
    When updating, performs: fetch -> checkout ref -> reset --hard to origin/ref.

    Args:
        repo_url: URL of the skills repository.
        branch: Branch name to checkout and track.
        cache_dir: Directory where the repository should be cached.

    Returns:
        Path to the local repository if successful, None otherwise.
    """
    repo_path = cache_dir / "public-skills"
    return try_cached_clone_or_update(repo_url, repo_path, ref=branch, update=True)


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
    from openhands.sdk.skills.skill import SkillResources

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
                files.append(rel_path.as_posix())
    except OSError as e:
        logger.warning(f"Error listing {resource_type} directory: {e}")
    return sorted(files)
