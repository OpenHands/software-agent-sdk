"""Utility functions for skill loading and management."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from fastmcp.mcp_config import MCPConfig

from openhands.sdk.git.cached_repo import GitHelper, try_cached_clone_or_update
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.exceptions import SkillValidationError
from openhands.sdk.utils.path import to_posix_path


if TYPE_CHECKING:
    from openhands.sdk.skills.skill import Skill, SkillResources

# Type alias for secret lookup functions
SecretLookup = Callable[[str], str | None]

logger = get_logger(__name__)

# Regex patterns for variable expansion
# Braced only: ${VAR} or ${VAR:-default}
_BRACED_VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")
# Braced and unbraced: $VAR, ${VAR}, or ${VAR:-default}
_ALL_VAR_PATTERN = re.compile(
    r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}|\$([a-zA-Z_][a-zA-Z0-9_]*)"
)


def expand_variable_references(
    data: Any,
    *,
    variables: dict[str, str] | None = None,
    get_secret: SecretLookup | None = None,
    check_env: bool = False,
    expand_defaults: bool = True,
    support_unbraced: bool = False,
) -> Any:
    """Expand variable references in data structures.

    This is the core expansion function used by both MCP config expansion
    and runtime tool parameter expansion.

    Supports variable expansion patterns:
    - ${VAR} - Braced variable reference (always supported)
    - ${VAR:-default} - With default value (always supported)
    - $VAR - Unbraced variable reference (only if support_unbraced=True)

    Resolution order (each source is checked only if provided/enabled):
    1. Provided variables dict
    2. Secrets (via get_secret callback)
    3. Environment variables (only if check_env=True)
    4. Default value (only if expand_defaults=True)

    Args:
        data: Data to expand (string, dict, list, or other).
        variables: Dictionary of variable names to values.
        get_secret: Callback to look up a secret by name.
        check_env: If True, check os.environ for unresolved variables.
        expand_defaults: If True, apply default values for unresolved variables.
        support_unbraced: If True, support $VAR syntax in addition to ${VAR}.

    Returns:
        Data with variable references expanded.
    """
    pattern = _ALL_VAR_PATTERN if support_unbraced else _BRACED_VAR_PATTERN

    def replace_var(match: re.Match) -> str:
        # For braced pattern: group(1) = var_name, group(2) = default
        # For all pattern: group(1)=braced, group(2)=default, group(3)=unbraced
        if support_unbraced:
            braced_var = match.group(1)
            default_value = match.group(2)
            unbraced_var = match.group(3)
            var_name = braced_var or unbraced_var
        else:
            var_name = match.group(1)
            default_value = match.group(2)

        # Resolution order: variables -> secrets -> env -> default
        if variables is not None and var_name in variables:
            return variables[var_name]

        if get_secret is not None:
            secret_value = get_secret(var_name)
            if secret_value is not None:
                return secret_value

        if check_env and var_name in os.environ:
            return os.environ[var_name]

        # Apply default only if expand_defaults is True and we have a default
        if expand_defaults and default_value is not None:
            return default_value

        # Return original if not found (preserves placeholder)
        return match.group(0)

    def expand_value(value: Any) -> Any:
        match value:
            case str():
                return pattern.sub(replace_var, value)
            case dict():
                return {
                    expand_value(k) if isinstance(k, str) else k: expand_value(v)
                    for k, v in value.items()
                }
            case list():
                return [expand_value(item) for item in value]
            case _:
                return value

    return expand_value(data)


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
    # sorted() ensures deterministic case-collision winner (SKILL.md < skill.md).
    for item in sorted(skill_dir.iterdir()):
        if item.is_file() and item.name.lower() == "skill.md":
            return item
    return None


# Plugin manifest locations. Mirrors ``PLUGIN_MANIFEST_DIRS`` /
# ``PLUGIN_MANIFEST_FILE`` in ``openhands.sdk.plugin.plugin``; duplicated here
# (rather than imported) because that module imports from this one, which would
# create a circular import. Used only to detect plugin folders mistakenly
# dropped into a skills directory so we can warn the user.
PLUGIN_MANIFEST_DIRS = (".plugin", ".claude-plugin")
PLUGIN_MANIFEST_FILE = "plugin.json"


def find_plugin_manifest(plugin_dir: Path) -> Path | None:
    """Find a plugin manifest (``plugin.json``) inside a directory.

    Checks both ``.plugin/`` and ``.claude-plugin/`` subdirectories.

    Args:
        plugin_dir: Path to the directory to inspect.

    Returns:
        Path to the manifest if found, None otherwise.
    """
    if not plugin_dir.is_dir():
        return None
    for manifest_dir in PLUGIN_MANIFEST_DIRS:
        candidate = plugin_dir / manifest_dir / PLUGIN_MANIFEST_FILE
        if candidate.is_file():
            return candidate
    return None


def warn_on_plugin_dirs(skill_dir: Path, loaded_skill_dirs: set[Path]) -> list[Path]:
    """Warn about plugin folders found inside a skills directory.

    Skills directories do not auto-load plugins. A folder containing only a
    plugin manifest (``.claude-plugin/plugin.json`` or ``.plugin/plugin.json``)
    but no ``SKILL.md`` would otherwise be silently ignored, which is confusing
    for users coming from Claude Code. This emits one actionable warning per
    such folder, pointing them at the explicit ``PluginSource`` load path.

    Args:
        skill_dir: The skills directory being scanned.
        loaded_skill_dirs: Directories already recognized as skills (skipped so
            a plugin that also ships a top-level ``SKILL.md`` isn't flagged).

    Returns:
        List of plugin directories that triggered a warning.
    """
    if not skill_dir.is_dir():
        return []
    flagged: list[Path] = []
    for subdir in sorted(p for p in skill_dir.iterdir() if p.is_dir()):
        if subdir in loaded_skill_dirs:
            continue
        manifest = find_plugin_manifest(subdir)
        if manifest is None:
            continue
        flagged.append(subdir)
        logger.warning(
            "Found a plugin manifest at '%s' inside skills directory '%s', but "
            "skills directories do not auto-load plugins; this folder is being "
            "ignored. Load the plugin explicitly, e.g. "
            'plugins=[PluginSource(source="%s")].',
            manifest,
            skill_dir,
            subdir,
        )
    return flagged


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
        config: MCP configuration dictionary.
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
    # Use the shared expansion function with MCP config settings:
    # - check_env=True (check environment variables)
    # - support_unbraced=False (only ${VAR} syntax for config files)
    expanded_config = expand_variable_references(
        config,
        variables=variables,
        get_secret=get_secret,
        check_env=True,
        expand_defaults=expand_defaults,
        support_unbraced=False,
    )

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

    # Validate the external .mcp.json shape using FastMCP's config model.
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
    # sorted() so an AGENTS.md/agents.md collision and the order are deterministic.
    for item in sorted(repo_root.iterdir()):
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


def _git_worktree_relpaths(work_dir: Path) -> list[PurePosixPath] | None:
    """Return worktree file paths under ``work_dir`` (relative to it): tracked
    plus untracked files that ``.gitignore`` does not exclude, so a freshly
    written (uncommitted) file still counts. None when git is unavailable, so the
    caller can walk instead."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(work_dir),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        text = proc.stdout.decode("utf-8", "surrogateescape")
        return [PurePosixPath(p) for p in text.split("\0") if p]
    return None


def _walk_relpaths(work_dir: Path) -> list[PurePosixPath]:
    """Filesystem-walk fallback: file paths under ``work_dir`` (relative to it),
    skipping hidden and ``node_modules`` directories. Used only when git is
    unavailable (the git path relies on ``.gitignore`` instead)."""
    results: list[PurePosixPath] = []
    for dirpath, dirnames, filenames in os.walk(work_dir):
        # Prune in place so os.walk does not descend into skipped directories.
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d != "node_modules"
        ]
        rel_dir = Path(dirpath).relative_to(work_dir)
        for filename in filenames:
            results.append(PurePosixPath((rel_dir / filename).as_posix()))
    return results


def find_nested_third_party_files(
    work_dir: Path, third_party_skill_names: dict[str, str]
) -> list[tuple[Path, PurePosixPath]]:
    """Find third-party instruction files *nested* under ``work_dir`` (top-level
    ones are handled by :func:`find_third_party_files`), so each can become a
    directory-scoped path rule. Uses ``git ls-files`` when available, else a
    pruned walk. Returns ``(absolute_path, relative_dir)`` tuples with
    ``relative_dir`` POSIX-relative to ``work_dir``."""
    if not work_dir.exists():
        return []

    target_names = {name.lower() for name in third_party_skill_names}
    rel_paths = _git_worktree_relpaths(work_dir)
    if rel_paths is None:
        rel_paths = _walk_relpaths(work_dir)

    results: list[tuple[Path, PurePosixPath]] = []
    seen_real_paths: set[Path] = set()
    for rel in rel_paths:
        if rel.name.lower() not in target_names:
            continue
        rel_dir = rel.parent
        # Skip top-level files: those belong to find_third_party_files.
        if rel_dir == PurePosixPath("."):
            continue
        abs_path = work_dir / rel
        if not abs_path.is_file():
            continue
        real_path = abs_path.resolve()
        if real_path in seen_real_paths:
            continue
        seen_real_paths.add(real_path)
        results.append((abs_path, rel_dir))
    return sorted(results, key=lambda pair: pair[1].as_posix())


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
    for subdir in sorted(skill_dir.iterdir()):
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
    for f in sorted(skill_dir.rglob("*.md")):
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
    ref: str,
    cache_dir: Path,
) -> Path | None:
    """Clone or update the local skills repository.

    Uses the shared git caching infrastructure from openhands.sdk.git.cached_repo.
    When updating, performs: fetch -> checkout ref -> reset --hard to origin/ref.

    Args:
        repo_url: URL of the skills repository.
        ref: Branch name, tag, or full commit SHA to checkout.
        cache_dir: Directory where the repository should be cached.

    Returns:
        Path to the local repository if successful, None otherwise.
    """
    repo_path = cache_dir / "public-skills"
    return try_cached_clone_or_update(repo_url, repo_path, ref=ref, update=True)


def is_skills_repo_pinned(repo_path: Path) -> bool:
    """Return True if the local skills repo is pinned to a fixed ref.

    A pinned ref is one that cannot change over time — a tag or a specific
    commit SHA. After checking out such a ref the repository is left in
    detached HEAD state, which is the signal used here.

    Returns False on any git error so callers can safely treat the result
    as ``False`` (i.e., keep polling) when the state cannot be determined.
    """
    try:
        return GitHelper().get_current_branch(repo_path) is None
    except Exception:
        return False


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

    resources = SkillResources(skill_root=to_posix_path(skill_dir.resolve()))

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
                files.append(to_posix_path(rel_path))
    except OSError as e:
        logger.warning(f"Error listing {resource_type} directory: {e}")
    return sorted(files)
