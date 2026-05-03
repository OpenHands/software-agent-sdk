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

# Exceptions
from openhands.sdk.skills.exceptions import SkillError, SkillValidationError

# Fetch utilities
from openhands.sdk.skills.fetch import SkillFetchError, fetch_skill_with_resolution

# Installed skills management
from openhands.sdk.skills.installed import (
    InstalledSkillInfo,
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

# Core skill model and loading
from openhands.sdk.skills.skill import (
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

# Triggers
from openhands.sdk.skills.trigger import (
    BaseTrigger,
    KeywordTrigger,
    TaskTrigger,
)

# Types
from openhands.sdk.skills.types import (
    InputMetadata,
    SkillContentResponse,
    SkillKnowledge,
    SkillResponse,
)

# Utilities
from openhands.sdk.skills.utils import (
    RESOURCE_DIRECTORIES,
    discover_skill_resources,
    validate_skill_name,
)


__all__ = [
    # Exceptions
    "SkillError",
    "SkillValidationError",
    # Fetch
    "SkillFetchError",
    "fetch_skill_with_resolution",
    # Installed skills management
    "InstalledSkillInfo",
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
    # Core skill model and loading
    "Skill",
    "SkillInfo",
    "SkillResources",
    "load_skills_from_dir",
    "load_project_skills",
    "load_user_skills",
    "load_public_skills",
    "load_available_skills",
    "to_prompt",
    # Triggers
    "BaseTrigger",
    "KeywordTrigger",
    "TaskTrigger",
    # Types
    "SkillKnowledge",
    "InputMetadata",
    "SkillResponse",
    "SkillContentResponse",
    # Utilities
    "discover_skill_resources",
    "RESOURCE_DIRECTORIES",
    "validate_skill_name",
]
