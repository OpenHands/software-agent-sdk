"""Installed skills management for OpenHands SDK.

Public API for managing AgentSkills installed in the user's home directory.
All heavy lifting is delegated to ``InstallationManager``.
"""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.extensions.installation import (
    InstallationInfo,
    InstallationInterface,
    InstallationManager,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.exceptions import SkillValidationError
from openhands.sdk.skills.skill import Skill
from openhands.sdk.skills.utils import find_skill_md


logger = get_logger(__name__)

# Public type alias — keeps existing import sites working.
InstalledSkillInfo = InstallationInfo

DEFAULT_INSTALLED_SKILLS_DIR = Path.home() / ".openhands" / "skills" / "installed"


def get_installed_skills_dir() -> Path:
    """Get the default directory for installed skills."""
    return DEFAULT_INSTALLED_SKILLS_DIR


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_skill_from_dir(skill_root: Path) -> Skill:
    """Load a skill from its root directory."""
    skill_md = find_skill_md(skill_root)
    if not skill_md:
        raise SkillValidationError(f"Skill directory is missing SKILL.md: {skill_root}")
    return Skill.load(skill_md, strict=True)


class SkillInstallationInterface(InstallationInterface[Skill]):
    @staticmethod
    def load_from_dir(extension_dir: Path) -> Skill:
        return _load_skill_from_dir(extension_dir)


def _resolve_installed_dir(installed_dir: Path | None) -> Path:
    return installed_dir if installed_dir is not None else DEFAULT_INSTALLED_SKILLS_DIR


def _manager(installed_dir: Path) -> InstallationManager[Skill]:
    return InstallationManager(
        installation_dir=installed_dir,
        installation_interface=SkillInstallationInterface(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_skill(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    installed_dir: Path | None = None,
    force: bool = False,
) -> InstalledSkillInfo:
    """Install a skill from a source.

    Args:
        source: Skill source — git URL, GitHub shorthand, or local path.
        ref: Optional branch, tag, or commit to install.
        repo_path: Subdirectory path within the repository (for monorepos).
        installed_dir: Directory for installed skills.
            Defaults to ``~/.openhands/skills/installed/``.
        force: If True, overwrite existing installation.

    Returns:
        InstalledSkillInfo with details about the installation.
    """
    return _manager(_resolve_installed_dir(installed_dir)).install(
        source, ref=ref, repo_path=repo_path, force=force
    )


def uninstall_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Uninstall a skill by name.

    Returns:
        True if the skill was uninstalled, False if it wasn't installed.
    """
    return _manager(_resolve_installed_dir(installed_dir)).uninstall(name)


def enable_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Enable an installed skill by name."""
    return _manager(_resolve_installed_dir(installed_dir)).enable(name)


def disable_skill(
    name: str,
    installed_dir: Path | None = None,
) -> bool:
    """Disable an installed skill by name."""
    return _manager(_resolve_installed_dir(installed_dir)).disable(name)


def list_installed_skills(
    installed_dir: Path | None = None,
) -> list[InstalledSkillInfo]:
    """List all installed skills.

    Self-healing: reconciles metadata with what is on disk.
    """
    return _manager(_resolve_installed_dir(installed_dir)).list_installed()


def load_installed_skills(
    installed_dir: Path | None = None,
) -> list[Skill]:
    """Load all enabled installed skills as ``Skill`` objects."""
    return _manager(_resolve_installed_dir(installed_dir)).load_installed()


def get_installed_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Get information about a specific installed skill."""
    return _manager(_resolve_installed_dir(installed_dir)).get(name)


def update_skill(
    name: str,
    installed_dir: Path | None = None,
) -> InstalledSkillInfo | None:
    """Update an installed skill to the latest version."""
    return _manager(_resolve_installed_dir(installed_dir)).update(name)


def install_skills_from_marketplace(
    marketplace_path: str | Path,
    installed_dir: Path | None = None,
    force: bool = False,
) -> list[InstalledSkillInfo]:
    """Install all skills defined in a marketplace.json file.

    Args:
        marketplace_path: Path to the directory containing
            ``.plugin/marketplace.json``.
        installed_dir: Directory for installed skills.
            Defaults to ``~/.openhands/skills/installed/``.
        force: If True, overwrite existing installations.

    Returns:
        List of InstalledSkillInfo for successfully installed skills.
    """
    from openhands.sdk.extensions.source import resolve_source_path
    from openhands.sdk.marketplace import Marketplace

    marketplace_path = Path(marketplace_path)
    installed_dir = _resolve_installed_dir(installed_dir)

    marketplace = Marketplace.load(marketplace_path)
    installed: list[InstalledSkillInfo] = []

    skill_dirs: list[tuple[str, Path]] = []

    for entry in marketplace.skills:
        resolved = resolve_source_path(
            entry.source, base_path=marketplace_path, update=True
        )
        if resolved and resolved.exists():
            skill_dirs.append((entry.name, resolved))
        else:
            logger.warning(f"Failed to resolve skill '{entry.name}'")

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

        skills_dir = resolved / "skills"
        if not skills_dir.exists():
            continue

        for skill_path in skills_dir.iterdir():
            if skill_path.is_dir() and (skill_path / "SKILL.md").exists():
                skill_dirs.append((skill_path.name, skill_path))

    logger.info(f"Found {len(skill_dirs)} skills to install from marketplace")

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
