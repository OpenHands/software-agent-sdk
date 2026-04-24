"""Skill management for OpenHands SDK.

This module provides the unified API for working with skills:

**Core Skill Model & Loading:**
- `Skill` - The skill data model
- `SkillResources` - Resource directories for a skill (scripts/, references/, assets/)
- `load_skills_from_dir` - Load skills from a directory
- `load_project_skills` - Load skills from project's .agents/skills/
- `load_user_skills` - Load skills from ~/.openhands/skills/
- `load_public_skills` - Load skills from the public OpenHands extensions repo
- `load_available_skills` - Load and merge skills from multiple sources

**Triggers:**
- `BaseTrigger`, `KeywordTrigger`, `TaskTrigger` - Skill activation triggers

**Installed Skills Management:**
- `install_skill` - Install a skill from a source
- `uninstall_skill` - Uninstall a skill
- `list_installed_skills` - List all installed skills
- `load_installed_skills` - Load enabled installed skills
- `enable_skill`, `disable_skill` - Toggle skill enabled state
- `update_skill` - Update an installed skill

**Types:**
- `SkillKnowledge` - Represents knowledge from a triggered skill
- `InputMetadata` - Metadata for task skill inputs

**Utilities:**
- `discover_skill_resources` - Discover resource directories in a skill
- `validate_skill_name` - Validate skill name per AgentSkills spec
- `to_prompt` - Generate XML prompt block for available skills
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .exceptions import SkillError, SkillValidationError
    from .fetch import SkillFetchError, fetch_skill_with_resolution
    from .installed import (
        InstalledSkillInfo,
        InstalledSkillsMetadata,
        disable_skill,
        enable_skill,
        get_installed_skill,
        get_installed_skills_dir,
        install_skill,
        install_skills_from_marketplace,
        list_installed_skills,
        load_installed_skills,
        uninstall_skill,
        update_skill,
    )
    from .skill import (
        Skill,
        SkillInfo,
        SkillResources,
        load_available_skills,
        load_project_skills,
        load_public_skills,
        load_skills_from_dir,
        load_user_skills,
        to_prompt,
    )
    from .trigger import BaseTrigger, KeywordTrigger, TaskTrigger
    from .types import (
        InputMetadata,
        SkillContentResponse,
        SkillKnowledge,
        SkillResponse,
    )
    from .utils import (
        RESOURCE_DIRECTORIES,
        discover_skill_resources,
        validate_skill_name,
    )


__all__ = [
    "SkillError",
    "SkillValidationError",
    "SkillFetchError",
    "fetch_skill_with_resolution",
    "InstalledSkillInfo",
    "InstalledSkillsMetadata",
    "install_skill",
    "install_skills_from_marketplace",
    "uninstall_skill",
    "list_installed_skills",
    "load_installed_skills",
    "get_installed_skills_dir",
    "get_installed_skill",
    "enable_skill",
    "disable_skill",
    "update_skill",
    "Skill",
    "SkillInfo",
    "SkillResources",
    "load_skills_from_dir",
    "load_project_skills",
    "load_user_skills",
    "load_public_skills",
    "load_available_skills",
    "to_prompt",
    "BaseTrigger",
    "KeywordTrigger",
    "TaskTrigger",
    "SkillKnowledge",
    "InputMetadata",
    "SkillResponse",
    "SkillContentResponse",
    "discover_skill_resources",
    "RESOURCE_DIRECTORIES",
    "validate_skill_name",
]

_LAZY_IMPORTS = {
    "SkillError": (".exceptions", "SkillError"),
    "SkillValidationError": (".exceptions", "SkillValidationError"),
    "SkillFetchError": (".fetch", "SkillFetchError"),
    "fetch_skill_with_resolution": (".fetch", "fetch_skill_with_resolution"),
    "InstalledSkillInfo": (".installed", "InstalledSkillInfo"),
    "InstalledSkillsMetadata": (".installed", "InstalledSkillsMetadata"),
    "install_skill": (".installed", "install_skill"),
    "install_skills_from_marketplace": (
        ".installed",
        "install_skills_from_marketplace",
    ),
    "uninstall_skill": (".installed", "uninstall_skill"),
    "list_installed_skills": (".installed", "list_installed_skills"),
    "load_installed_skills": (".installed", "load_installed_skills"),
    "get_installed_skills_dir": (".installed", "get_installed_skills_dir"),
    "get_installed_skill": (".installed", "get_installed_skill"),
    "enable_skill": (".installed", "enable_skill"),
    "disable_skill": (".installed", "disable_skill"),
    "update_skill": (".installed", "update_skill"),
    "Skill": (".skill", "Skill"),
    "SkillInfo": (".skill", "SkillInfo"),
    "SkillResources": (".skill", "SkillResources"),
    "load_skills_from_dir": (".skill", "load_skills_from_dir"),
    "load_project_skills": (".skill", "load_project_skills"),
    "load_user_skills": (".skill", "load_user_skills"),
    "load_public_skills": (".skill", "load_public_skills"),
    "load_available_skills": (".skill", "load_available_skills"),
    "to_prompt": (".skill", "to_prompt"),
    "BaseTrigger": (".trigger", "BaseTrigger"),
    "KeywordTrigger": (".trigger", "KeywordTrigger"),
    "TaskTrigger": (".trigger", "TaskTrigger"),
    "SkillKnowledge": (".types", "SkillKnowledge"),
    "InputMetadata": (".types", "InputMetadata"),
    "SkillResponse": (".types", "SkillResponse"),
    "SkillContentResponse": (".types", "SkillContentResponse"),
    "discover_skill_resources": (".utils", "discover_skill_resources"),
    "RESOURCE_DIRECTORIES": (".utils", "RESOURCE_DIRECTORIES"),
    "validate_skill_name": (".utils", "validate_skill_name"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
