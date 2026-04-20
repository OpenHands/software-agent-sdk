"""Deprecated: Use openhands.sdk.skills instead.

This module is deprecated and will be removed in a future version.
All skill-related imports should come from `openhands.sdk.skills`.

Migration guide:
    # Old (deprecated):
    from openhands.sdk.context.skills import Skill, load_skills_from_dir

    # New:
    from openhands.sdk.skills import Skill, load_skills_from_dir
"""

from openhands.sdk.utils.deprecation import warn_deprecated


def __getattr__(name: str):
    """Lazily import from openhands.sdk.skills with deprecation warning."""
    # Import the canonical module
    from openhands.sdk import skills as _skills

    # List of valid exports that we re-export with deprecation warning
    _VALID_EXPORTS = {
        # Exceptions
        "SkillError",
        "SkillValidationError",
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
    }

    if name in _VALID_EXPORTS:
        warn_deprecated(
            f"Importing '{name}' from 'openhands.sdk.context.skills'",
            deprecated_in="1.16.0",
            removed_in="1.20.0",
            details=f"Use 'from openhands.sdk.skills import {name}' instead.",
            stacklevel=3,
        )
        return getattr(_skills, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """List available attributes for tab-completion."""
    return [
        # Exceptions
        "SkillError",
        "SkillValidationError",
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


__all__ = [
    # Exceptions
    "SkillError",
    "SkillValidationError",
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
