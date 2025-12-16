"""Load skills from installed OpenHands skill packages.

This module provides functionality to discover and load skills from
installed Python packages that register as OpenHands skill packages
via entry points.

Packages must use manifest.json (Claude Code-aligned) descriptor format.
"""

import json
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from openhands.sdk.context.skills.skill import Skill
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def _load_descriptor(package_module) -> dict[str, Any]:
    """Load package descriptor from manifest.json.

    Args:
        package_module: The loaded package module

    Returns:
        Parsed descriptor dictionary

    Raises:
        FileNotFoundError: If manifest.json is not found
        json.JSONDecodeError: If manifest.json is invalid
    """
    json_content = files(package_module).joinpath("manifest.json").read_text()
    return json.loads(json_content)


def list_skill_packages() -> list[dict[str, Any]]:
    """Discover all installed OpenHands skill packages.

    Uses Python's entry points mechanism for fast discovery without
    filesystem scanning. Packages register themselves via the
    'openhands.skill_packages' entry point group.

    Returns:
        List of dicts with 'name' (str) and 'descriptor' (dict) keys.
        The descriptor contains the parsed manifest.json content.

    Example:
        >>> packages = list_skill_packages()
        >>> for pkg in packages:
        ...     name = pkg['name']
        ...     display_name = pkg['descriptor'].get('displayName', 'Unknown')
        ...     print(f"{name}: {display_name}")
    """
    eps = entry_points(group="openhands.skill_packages")

    packages = []

    for ep in eps:
        try:
            # Load the package module
            package_module = ep.load()

            # Load descriptor from manifest.json
            descriptor = _load_descriptor(package_module)

            packages.append(
                {"name": ep.name, "descriptor": descriptor, "module": package_module}
            )
        except Exception as e:
            # Log warning but continue - don't fail if one package is broken
            logger.warning(f"Failed to load skill package {ep.name}: {e}")

    return packages


def get_skill_package(package_name: str) -> dict[str, Any] | None:
    """Get a specific skill package by name.

    Args:
        package_name: The name of the package to retrieve (from entry point name)

    Returns:
        Dict with 'name', 'descriptor', and 'module' keys, or None if not found.

    Example:
        >>> pkg = get_skill_package('simple-code-review')
        >>> if pkg:
        ...     print(pkg['descriptor'].get('displayName', 'Unknown'))
    """
    eps = entry_points(group="openhands.skill_packages")

    for ep in eps:
        if ep.name == package_name:
            try:
                # Load the package module
                package_module = ep.load()

                # Load descriptor from manifest.json
                descriptor = _load_descriptor(package_module)

                return {
                    "name": ep.name,
                    "descriptor": descriptor,
                    "module": package_module,
                }
            except Exception as e:
                logger.error(f"Failed to load skill package {package_name}: {e}")
                return None

    return None


def load_skills_from_package(
    package_name: str,
) -> tuple[dict[str, Skill], dict[str, Skill]]:
    """Load skills from a specific skill package.

    Args:
        package_name: Name of the package (entry point name, e.g., 'simple-code-review')

    Returns:
        Tuple of (repo_skills, knowledge_skills) dictionaries.
        repo_skills have trigger=None, knowledge_skills have KeywordTrigger
        or TaskTrigger.

    Raises:
        ValueError: If package is not found or cannot be loaded

    Example:
        >>> repo_skills, knowledge_skills = load_skills_from_package(
        ...     'simple-code-review'
        ... )
        >>> for name, skill in knowledge_skills.items():
        ...     print(f"Loaded skill: {name}")
    """
    package = get_skill_package(package_name)
    if package is None:
        raise ValueError(f"Skill package '{package_name}' not found. Is it installed?")

    descriptor = package["descriptor"]
    package_module = package["module"]

    repo_skills = {}
    knowledge_skills = {}

    # Load skills defined in the descriptor
    # manifest.json format: skills are at top level
    skills_spec = descriptor.get("skills", [])

    logger.debug(f"Loading {len(skills_spec)} skills from package '{package_name}'")

    for skill_spec in skills_spec:
        skill_name = skill_spec.get("name")
        skill_path = skill_spec.get("path")

        if not skill_name or not skill_path:
            logger.warning(
                f"Skipping invalid skill spec in package '{package_name}': {skill_spec}"
            )
            continue

        try:
            # Read the skill file from the package
            skill_content = files(package_module).joinpath(skill_path).read_text()

            # Parse the skill file (it may have frontmatter)
            # The skill file path becomes the source
            source = f"package:{package_name}/{skill_path}"

            # Use Skill.load to parse the skill file properly
            # We need to create a temporary path for load() to work
            # Actually, let's parse it directly using frontmatter
            import io

            import frontmatter as fm

            parsed = fm.load(io.StringIO(skill_content))
            content = parsed.content
            metadata = parsed.metadata

            # Determine trigger type from metadata
            trigger = None
            if "triggers" in metadata:
                from openhands.sdk.context.skills.trigger import KeywordTrigger

                trigger = KeywordTrigger(keywords=metadata["triggers"])

            # Create the Skill object
            skill = Skill(
                name=skill_name, content=content, trigger=trigger, source=source
            )

            # Categorize based on trigger
            if skill.trigger is None:
                repo_skills[skill_name] = skill
            else:
                knowledge_skills[skill_name] = skill

            logger.debug(f"Loaded skill '{skill_name}' from package '{package_name}'")

        except Exception as e:
            logger.error(
                f"Failed to load skill '{skill_name}' from '{package_name}': {e}"
            )
            continue

    logger.info(
        f"Loaded {len(repo_skills)} repo skills and {len(knowledge_skills)} "
        f"knowledge skills from package '{package_name}'"
    )

    return repo_skills, knowledge_skills


def load_skills_from_packages(
    package_names: list[str],
) -> tuple[dict[str, Skill], dict[str, Skill]]:
    """Load skills from multiple skill packages.

    Args:
        package_names: List of package names to load

    Returns:
        Tuple of (repo_skills, knowledge_skills) dictionaries containing
        all skills from all packages.

    Example:
        >>> packages = ['simple-code-review', 'awesome-skills']
        >>> repo_skills, knowledge_skills = load_skills_from_packages(packages)
    """
    all_repo_skills = {}
    all_knowledge_skills = {}

    for package_name in package_names:
        try:
            repo_skills, knowledge_skills = load_skills_from_package(package_name)
            all_repo_skills.update(repo_skills)
            all_knowledge_skills.update(knowledge_skills)
        except ValueError as e:
            logger.error(str(e))
        except Exception as e:
            logger.error(f"Unexpected error loading package '{package_name}': {e}")

    return all_repo_skills, all_knowledge_skills


def load_packages_config(config_path: Path | str | None = None) -> list[str]:
    """Load package configuration from .openhands/packages.yaml.

    Args:
        config_path: Path to packages.yaml. If None, looks for:
                    - .openhands/packages.yaml in current directory
                    - ~/.openhands/packages.yaml in home directory

    Returns:
        List of package names to load. Empty list if no config found.

    Example:
        >>> packages = load_packages_config()
        >>> print(f"Configured packages: {packages}")
    """
    if config_path is None:
        # Try current directory first
        local_config = Path(".openhands/packages.yaml")
        home_config = Path.home() / ".openhands" / "packages.yaml"

        if local_config.exists():
            config_path = local_config
        elif home_config.exists():
            config_path = home_config
        else:
            logger.debug("No packages.yaml configuration found")
            return []
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        logger.warning(f"Package configuration not found: {config_path}")
        return []

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)

        packages = config.get("packages", [])
        logger.info(f"Loaded package configuration from {config_path}: {packages}")
        return packages

    except Exception as e:
        logger.error(f"Failed to load package configuration from {config_path}: {e}")
        return []


def load_configured_package_skills() -> tuple[dict[str, Skill], dict[str, Skill]]:
    """Load skills from all configured packages.

    Reads .openhands/packages.yaml to determine which packages to load,
    then loads skills from those packages.

    Returns:
        Tuple of (repo_skills, knowledge_skills) dictionaries.

    Example:
        >>> repo_skills, knowledge_skills = load_configured_package_skills()
        >>> print(f"Loaded {len(knowledge_skills)} knowledge skills from packages")
    """
    package_names = load_packages_config()

    if not package_names:
        logger.debug("No skill packages configured")
        return {}, {}

    return load_skills_from_packages(package_names)
