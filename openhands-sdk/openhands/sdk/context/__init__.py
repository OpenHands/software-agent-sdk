from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from ..skills.exceptions import SkillValidationError
    from ..skills.skill import (
        Skill,
        load_project_skills,
        load_skills_from_dir,
        load_user_skills,
    )
    from ..skills.trigger import BaseTrigger, KeywordTrigger, TaskTrigger
    from ..skills.types import SkillKnowledge
    from .agent_context import AgentContext
    from .prompts import render_template


__all__ = [
    "AgentContext",
    "Skill",
    "BaseTrigger",
    "KeywordTrigger",
    "TaskTrigger",
    "SkillKnowledge",
    "load_skills_from_dir",
    "load_user_skills",
    "load_project_skills",
    "render_template",
    "SkillValidationError",
]

_LAZY_IMPORTS = {
    "AgentContext": (".agent_context", "AgentContext"),
    "Skill": ("..skills.skill", "Skill"),
    "BaseTrigger": ("..skills.trigger", "BaseTrigger"),
    "KeywordTrigger": ("..skills.trigger", "KeywordTrigger"),
    "TaskTrigger": ("..skills.trigger", "TaskTrigger"),
    "SkillKnowledge": ("..skills.types", "SkillKnowledge"),
    "load_skills_from_dir": ("..skills.skill", "load_skills_from_dir"),
    "load_user_skills": ("..skills.skill", "load_user_skills"),
    "load_project_skills": ("..skills.skill", "load_project_skills"),
    "render_template": (".prompts", "render_template"),
    "SkillValidationError": ("..skills.exceptions", "SkillValidationError"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
