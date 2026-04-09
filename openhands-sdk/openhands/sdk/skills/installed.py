"""Installed skills management for OpenHands SDK.

This module provides utilities for managing AgentSkills installed in the user's
home directory (~/.openhands/skills/installed/).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from openhands.sdk.extensions import (
    InstalledExtensionInfo,
    InstalledExtensionManager,
    InstalledExtensionMetadata,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.exceptions import SkillValidationError
from openhands.sdk.skills.skill import Skill, load_skills_from_dir
from openhands.sdk.skills.utils import find_skill_md, validate_skill_name


logger = get_logger(__name__)

DEFAULT_INSTALLED_SKILLS_DIR = Path.home() / ".openhands" / "skills" / "installed"


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


class InstalledSkillInfo(InstalledExtensionInfo):
    """Information about an installed skill.

    Extends InstalledExtensionInfo with AgentSkills-specific fields.
    """

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


# For backward compatibility, provide InstalledSkillsMetadata as a wrapper
# around InstalledExtensionMetadata.
class InstalledSkillsMetadata(InstalledExtensionMetadata[InstalledSkillInfo]):
    """Metadata file for tracking all installed skills.

    This class wraps InstalledExtensionMetadata for backward compatibility.
    New code should use the InstalledExtensionManager instead.
    """

    def __init__(
        self,
        *,
        items: dict[str, InstalledSkillInfo] | None = None,
        skills: dict[str, InstalledSkillInfo] | None = None,
    ) -> None:
        """Initialize with either items or skills keyword argument."""
        # Support both 'items' (new) and 'skills' (legacy) kwarg
        data = items if items is not None else (skills or {})
        super().__init__(items=data)

    # Alias 'items' as 'skills' for backward compatibility
    @property
    def skills(self) -> dict[str, InstalledSkillInfo]:
        """Get installed skills (alias for items)."""
        return self.items

    @skills.setter
    def skills(self, value: dict[str, InstalledSkillInfo]) -> None:
        """Set installed skills (alias for items)."""
        self.items = value

    @classmethod
    def load_from_dir(  # type: ignore[override]
        cls, installed_dir: Path
    ) -> InstalledSkillsMetadata:
        """Load metadata from the installed skills directory."""
        base = InstalledExtensionMetadata.load_from_dir(
            installed_dir, InstalledSkillInfo
        )
        return cls(items=base.items)

    def save_to_dir(self, installed_dir: Path) -> None:
        """Save metadata to the installed skills directory (legacy format)."""
        import json

        metadata_path = self.get_path(installed_dir)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        # Use "skills" key for backward compatibility
        data = {
            "skills": {name: info.model_dump() for name, info in self.items.items()}
        }
        with open(metadata_path, "w") as f:
            json.dump(data, f, indent=2)


def _create_skill_info(
    skill: Skill,
    source: str,
    resolved_ref: str | None,
    repo_path: str | None,
    install_path: Path,
) -> InstalledSkillInfo:
    """Create InstalledSkillInfo from a loaded Skill (for manager callback)."""
    return InstalledSkillInfo.from_skill(
        skill, source, resolved_ref, repo_path, install_path
    )


# Create the skill manager instance
_skill_manager: InstalledExtensionManager[Skill, InstalledSkillInfo] = (
    InstalledExtensionManager(
        default_dir=DEFAULT_INSTALLED_SKILLS_DIR,
        validate_name=_validate_skill_name,
        load_item=_load_skill_from_dir,
        create_info=_create_skill_info,
        info_type=InstalledSkillInfo,
    )
)


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
    return _skill_manager.install(
        source=source,
        ref=ref,
        repo_path=repo_path,
        installed_dir=installed_dir,
        force=force,
    )


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
        True if uninstalled successfully, False if not found.
    """
    return _skill_manager.uninstall(name=name, installed_dir=installed_dir)


def enable_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Enable an installed skill by name."""
    return _skill_manager.enable(name=name, installed_dir=installed_dir)


def disable_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Disable an installed skill by name."""
    return _skill_manager.disable(name=name, installed_dir=installed_dir)


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
    return _skill_manager.list_installed(installed_dir=installed_dir)


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
    resolved_dir = installed_dir or DEFAULT_INSTALLED_SKILLS_DIR

    if not resolved_dir.exists():
        return []

    # Get enabled skill names
    installed_infos = list_installed_skills(resolved_dir)
    enabled_names = {info.name for info in installed_infos if info.enabled}

    # Load all skills from directory using existing loader
    repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(resolved_dir)
    all_skills = {**repo_skills, **knowledge_skills, **agent_skills}

    return [skill for name, skill in all_skills.items() if name in enabled_names]


def get_installed_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Get information about a specific installed skill."""
    return _skill_manager.get(name=name, installed_dir=installed_dir)


def update_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Update an installed skill to the latest version."""
    return _skill_manager.update(name=name, installed_dir=installed_dir)


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
    from openhands.sdk.marketplace import Marketplace
    from openhands.sdk.plugin import resolve_source_path

    marketplace_path = Path(marketplace_path)
    resolved_dir = installed_dir or DEFAULT_INSTALLED_SKILLS_DIR

    # Load the marketplace
    marketplace = Marketplace.load(marketplace_path)

    installed: list[InstalledSkillInfo] = []

    # Collect skill directories: standalone skills + skills from plugins
    skill_dirs: list[tuple[str, Path]] = []  # (name, path)

    # 1. Standalone skills from marketplace.skills
    for entry in marketplace.skills:
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
            info = install_skill(str(path), installed_dir=resolved_dir, force=force)
            installed.append(info)
            logger.info(f"Installed skill '{info.name}'")
        except FileExistsError:
            logger.info(f"Skill '{name}' already installed (use force=True)")
        except Exception as e:
            logger.warning(f"Failed to install skill '{name}': {e}")

    logger.info(f"Installed {len(installed)} skills")
    return installed
