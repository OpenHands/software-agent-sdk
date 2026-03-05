"""Installed skills management for OpenHands SDK.

This module provides utilities for managing AgentSkills installed in the user's
home directory (~/.openhands/skills/installed/).
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from openhands.sdk.context.skills.exceptions import SkillError, SkillValidationError
from openhands.sdk.context.skills.skill import Skill, load_skills_from_dir
from openhands.sdk.context.skills.utils import find_skill_md, validate_skill_name
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.fetch import fetch_skill_with_resolution


logger = get_logger(__name__)

DEFAULT_INSTALLED_SKILLS_DIR = Path.home() / ".openhands" / "skills" / "installed"
_METADATA_FILENAME = ".installed.json"


def _resolve_installed_dir(installed_dir: Path | None) -> Path:
    """Return installed_dir or the default if None."""
    return installed_dir if installed_dir is not None else DEFAULT_INSTALLED_SKILLS_DIR


def get_installed_skills_dir() -> Path:
    """Get the default directory for installed skills.

    Returns:
        Path to ~/.openhands/skills/installed/
    """
    return DEFAULT_INSTALLED_SKILLS_DIR


def _validate_skill_name(name: str) -> None:
    """Validate skill name according to AgentSkills spec."""
    errors = validate_skill_name(name)
    if errors:
        raise ValueError(f"Invalid skill name {name!r}: {'; '.join(errors)}")


def _load_skill_from_dir(skill_root: Path) -> Skill:
    """Load a skill from its root directory."""
    skill_md = find_skill_md(skill_root)
    if not skill_md:
        raise SkillValidationError(f"Skill directory is missing SKILL.md: {skill_root}")
    return Skill.load(skill_md, strict=True)


class InstalledSkillInfo(BaseModel):
    """Information about an installed skill."""

    name: str = Field(description="Skill name")
    description: str = Field(default="", description="Skill description")
    license: str | None = Field(default=None, description="Skill license")
    compatibility: str | None = Field(
        default=None, description="Compatibility notes for the skill"
    )
    metadata: dict[str, str] | None = Field(
        default=None, description="Additional skill metadata"
    )
    allowed_tools: list[str] | None = Field(
        default=None, description="Allowed tools list for the skill"
    )
    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None,
        description="Resolved git commit SHA (for version pinning)",
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )
    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the skill is installed")

    @classmethod
    def from_skill(
        cls,
        skill: Skill,
        source: str,
        resolved_ref: str | None,
        repo_path: str | None,
        install_path: Path,
    ) -> InstalledSkillInfo:
        """Create InstalledSkillInfo from a loaded Skill."""
        return cls(
            name=skill.name,
            description=skill.description or "",
            license=skill.license,
            compatibility=skill.compatibility,
            metadata=skill.metadata,
            allowed_tools=skill.allowed_tools,
            source=source,
            resolved_ref=resolved_ref,
            repo_path=repo_path,
            installed_at=datetime.now(UTC).isoformat(),
            install_path=str(install_path),
        )


class InstalledSkillsMetadata(BaseModel):
    """Metadata file for tracking installed skills."""

    skills: dict[str, InstalledSkillInfo] = Field(
        default_factory=dict,
        description="Map of skill name to installation info",
    )

    @classmethod
    def get_path(cls, installed_dir: Path) -> Path:
        """Get the metadata file path for the given installed skills directory."""
        return installed_dir / _METADATA_FILENAME

    @classmethod
    def load_from_dir(cls, installed_dir: Path) -> InstalledSkillsMetadata:
        """Load metadata from the installed skills directory."""
        metadata_path = cls.get_path(installed_dir)
        if not metadata_path.exists():
            return cls()
        try:
            with open(metadata_path) as f:
                data = json.load(f)
            return cls.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to load installed skills metadata: {e}")
            return cls()

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed skills directory."""
        metadata_path = self.get_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with open(metadata_path, "w") as f:
            json.dump(self.model_dump(), f, indent=2)


def install_skill(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    installed_dir: Path | None = None,
    force: bool = False,
) -> InstalledSkillInfo:
    """Install a skill from a source.

    Args:
        source: Skill source - git URL, GitHub shorthand, or local path.
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory path within the repository (for monorepos).
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/
        force: If True, overwrite existing installation. If False, raise error
            if the skill is already installed.

    Returns:
        InstalledSkillInfo with details about the installation.

    Raises:
        SkillFetchError: If fetching the skill fails.
        FileExistsError: If skill is already installed and force=False.
        SkillValidationError: If the skill metadata is invalid.
    """
    installed_dir = _resolve_installed_dir(installed_dir)

    logger.info(f"Fetching skill from {source}")
    fetched_path, resolved_ref = fetch_skill_with_resolution(
        source=source,
        ref=ref,
        repo_path=repo_path,
        update=True,
    )

    skill = _load_skill_from_dir(fetched_path)
    skill_name = skill.name
    _validate_skill_name(skill_name)

    install_path = installed_dir / skill_name
    if install_path.exists() and not force:
        raise FileExistsError(
            f"Skill '{skill_name}' is already installed at {install_path}. "
            "Use force=True to overwrite."
        )

    if install_path.exists():
        logger.info(f"Removing existing installation of '{skill_name}'")
        shutil.rmtree(install_path)

    logger.info(f"Installing skill '{skill_name}' to {install_path}")
    installed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fetched_path, install_path)

    info = InstalledSkillInfo.from_skill(
        skill=skill,
        source=source,
        resolved_ref=resolved_ref,
        repo_path=repo_path,
        install_path=install_path,
    )

    metadata = InstalledSkillsMetadata.load_from_dir(installed_dir)
    metadata.skills[skill_name] = info
    metadata.save_to_dir(installed_dir)

    logger.info(f"Successfully installed skill '{skill_name}'")
    return info


def uninstall_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Uninstall a skill by name.

    Only skills tracked in the installed skills metadata file can be uninstalled.

    Args:
        name: Name of the skill to uninstall.
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/

    Returns:
        True if the skill was uninstalled, False if it wasn't installed.
    """
    _validate_skill_name(name)
    installed_dir = _resolve_installed_dir(installed_dir)

    metadata = InstalledSkillsMetadata.load_from_dir(installed_dir)
    if name not in metadata.skills:
        logger.warning(f"Skill '{name}' is not installed")
        return False

    skill_path = installed_dir / name
    if skill_path.exists():
        logger.info(f"Uninstalling skill '{name}' from {skill_path}")
        shutil.rmtree(skill_path)
    else:
        logger.warning(
            f"Skill '{name}' was tracked but its directory is missing: {skill_path}"
        )

    del metadata.skills[name]
    metadata.save_to_dir(installed_dir)

    logger.info(f"Successfully uninstalled skill '{name}'")
    return True


def _validate_tracked_skills(
    metadata: InstalledSkillsMetadata, installed_dir: Path
) -> tuple[list[InstalledSkillInfo], bool]:
    """Validate tracked skills exist on disk."""
    valid_skills: list[InstalledSkillInfo] = []
    changed = False

    for name, info in list(metadata.skills.items()):
        try:
            _validate_skill_name(name)
        except ValueError as e:
            logger.warning(f"Invalid tracked skill name {name!r}, removing: {e}")
            del metadata.skills[name]
            changed = True
            continue

        skill_path = installed_dir / name
        if skill_path.exists():
            valid_skills.append(info)
        else:
            logger.warning(f"Skill '{name}' directory missing, removing from metadata")
            del metadata.skills[name]
            changed = True

    return valid_skills, changed


def _discover_untracked_skills(
    metadata: InstalledSkillsMetadata, installed_dir: Path
) -> tuple[list[InstalledSkillInfo], bool]:
    """Discover skill directories not tracked in metadata."""
    discovered: list[InstalledSkillInfo] = []
    changed = False

    for item in installed_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        if item.name in metadata.skills:
            continue

        try:
            _validate_skill_name(item.name)
        except ValueError:
            logger.debug(f"Skipping directory with invalid skill name: {item}")
            continue

        try:
            skill = _load_skill_from_dir(item)
        except (SkillError, OSError) as e:
            logger.debug(f"Skipping directory {item}: {e}")
            continue

        if skill.name != item.name:
            logger.warning(
                "Skipping skill directory because name doesn't match directory: "
                f"dir={item.name!r}, skill={skill.name!r}"
            )
            continue

        info = InstalledSkillInfo(
            name=skill.name,
            description=skill.description or "",
            license=skill.license,
            compatibility=skill.compatibility,
            metadata=skill.metadata,
            allowed_tools=skill.allowed_tools,
            source="local",
            installed_at=datetime.now(UTC).isoformat(),
            install_path=str(item),
        )
        discovered.append(info)
        metadata.skills[item.name] = info
        changed = True
        logger.info(f"Discovered untracked skill: {skill.name}")

    return discovered, changed


def list_installed_skills(
    installed_dir: Path | None = None,
) -> list[InstalledSkillInfo]:
    """List all installed skills.

    This function is self-healing: it may update the installed skills metadata
    file to remove entries whose directories were deleted, and to add entries for
    skill directories that were manually copied into the installed dir.

    Args:
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/

    Returns:
        List of InstalledSkillInfo for each installed skill.
    """
    installed_dir = _resolve_installed_dir(installed_dir)

    if not installed_dir.exists():
        return []

    metadata = InstalledSkillsMetadata.load_from_dir(installed_dir)

    valid_skills, tracked_changed = _validate_tracked_skills(metadata, installed_dir)
    discovered, discovered_changed = _discover_untracked_skills(metadata, installed_dir)

    if tracked_changed or discovered_changed:
        metadata.save_to_dir(installed_dir)

    return valid_skills + discovered


def load_installed_skills(
    installed_dir: Path | None = None,
) -> list[Skill]:
    """Load all installed skills.

    Args:
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/

    Returns:
        List of loaded Skill objects.
    """
    installed_dir = _resolve_installed_dir(installed_dir)

    if not installed_dir.exists():
        return []

    repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(installed_dir)
    return [
        *repo_skills.values(),
        *knowledge_skills.values(),
        *agent_skills.values(),
    ]


def get_installed_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Get information about a specific installed skill."""
    _validate_skill_name(name)
    installed_dir = _resolve_installed_dir(installed_dir)

    metadata = InstalledSkillsMetadata.load_from_dir(installed_dir)
    info = metadata.skills.get(name)

    if info is not None:
        skill_path = installed_dir / name
        if not skill_path.exists():
            return None

    return info


def update_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Update an installed skill to the latest version."""
    _validate_skill_name(name)
    installed_dir = _resolve_installed_dir(installed_dir)

    current_info = get_installed_skill(name, installed_dir)
    if current_info is None:
        logger.warning(f"Skill '{name}' is not installed")
        return None

    logger.info(f"Updating skill '{name}' from {current_info.source}")
    return install_skill(
        source=current_info.source,
        ref=None,
        repo_path=current_info.repo_path,
        installed_dir=installed_dir,
        force=True,
    )


def install_skills_from_marketplace(
    marketplace_path: str | Path,
    installed_dir: Path | None = None,
    force: bool = False,
) -> list[InstalledSkillInfo]:
    """Install all skills defined in a marketplace.json file.

    This function reads the marketplace.json, resolves each skill source
    (supporting both local paths and GitHub URLs), and installs them to
    the installed skills directory.

    Args:
        marketplace_path: Path to the directory containing .plugin/marketplace.json
        installed_dir: Directory for installed skills.
            Defaults to ~/.openhands/skills/installed/
        force: If True, overwrite existing installations.

    Returns:
        List of InstalledSkillInfo for successfully installed skills.

    Raises:
        FileNotFoundError: If the marketplace.json doesn't exist.
        ValueError: If the marketplace.json is invalid.

    Example:
        >>> # Install all skills from a marketplace
        >>> installed = install_skills_from_marketplace("./my-marketplace")
        >>> for info in installed:
        ...     print(f"Installed: {info.name}")
    """
    from openhands.sdk.plugin import Marketplace, resolve_source_path

    marketplace_path = Path(marketplace_path)
    installed_dir = _resolve_installed_dir(installed_dir)

    # Load the marketplace
    marketplace = Marketplace.load(marketplace_path)

    installed: list[InstalledSkillInfo] = []

    # Collect skill directories: standalone skills + skills from plugins
    skill_dirs: list[tuple[str, Path]] = []  # (name, path)

    # 1. Standalone skills from marketplace.skills
    for entry in marketplace.skills:
        if not isinstance(entry.source, str):
            logger.warning(f"Skill '{entry.name}' has unsupported source type")
            continue

        resolved = resolve_source_path(
            entry.source, base_path=marketplace_path, update=True
        )
        if resolved and resolved.exists():
            skill_dirs.append((entry.name, resolved))
        else:
            logger.warning(f"Failed to resolve skill '{entry.name}'")

    # 2. Skills from plugins (each plugin's skills/ directory)
    for plugin in marketplace.plugins:
        if isinstance(plugin.source, str):
            source = plugin.source
        elif plugin.source.repo:
            source = f"https://github.com/{plugin.source.repo}.git"
        elif plugin.source.url:
            source = plugin.source.url
        else:
            logger.warning(f"Plugin '{plugin.name}' has unsupported source")
            continue

        resolved = resolve_source_path(source, base_path=marketplace_path, update=True)
        if not resolved or not resolved.exists():
            logger.warning(f"Failed to resolve plugin '{plugin.name}'")
            continue

        # Find skills/ directory in plugin
        skills_dir = resolved / "skills"
        if not skills_dir.exists():
            continue

        # Each subdirectory in skills/ is a skill
        for skill_path in skills_dir.iterdir():
            if skill_path.is_dir() and (skill_path / "SKILL.md").exists():
                skill_dirs.append((skill_path.name, skill_path))

    logger.info(f"Found {len(skill_dirs)} skills to install from marketplace")

    # Install all collected skills
    for name, path in skill_dirs:
        try:
            info = install_skill(str(path), installed_dir=installed_dir, force=force)
            installed.append(info)
            logger.info(f"Installed skill '{info.name}'")
        except FileExistsError:
            logger.info(f"Skill '{name}' already installed (use force=True)")
        except Exception as e:
            logger.warning(f"Failed to install skill '{name}': {e}")

    logger.info(f"Installed {len(installed)} skills")
    return installed
