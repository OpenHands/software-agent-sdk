from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.skills import Skill


ALWAYS_AVAILABLE_TOOL_NAMES = frozenset({"finish", "think"})

_ALLOWED_TOOL_ALIASES = {
    "bash": "terminal",
    "terminal": "terminal",
    "read": "file_editor",
    "write": "file_editor",
    "edit": "file_editor",
    "fileeditor": "file_editor",
    "file_editor": "file_editor",
    "grep": "grep",
    "glob": "glob",
}


@dataclass(frozen=True)
class EffectiveTools:
    tools_map: dict[str, ToolDefinition]
    restricted_skill_names: tuple[str, ...] = ()

    @property
    def is_restricted(self) -> bool:
        return bool(self.restricted_skill_names)


def _base_allowed_tool_name(raw_name: str) -> str:
    name = raw_name.strip()
    if "(" in name:
        name = name.split("(", 1)[0].strip()
    return name


def _normalize_allowed_tool_names(
    allowed_tools: list[str],
    available_tool_names: set[str],
) -> set[str]:
    normalized: set[str] = set()
    for raw_name in allowed_tools:
        name = _base_allowed_tool_name(raw_name)
        if not name:
            continue
        if name in available_tool_names:
            normalized.add(name)
            continue

        alias = _ALLOWED_TOOL_ALIASES.get(name.lower())
        if alias is not None and alias in available_tool_names:
            normalized.add(alias)
    return normalized


def _invoked_restricted_skills(state: ConversationState) -> list[Skill]:
    agent_context = state.agent.agent_context
    if agent_context is None:
        return []

    skills_by_name = {skill.name: skill for skill in agent_context.skills}
    restricted: list[Skill] = []
    for skill_name in state.invoked_skills:
        skill = skills_by_name.get(skill_name)
        if skill is not None and skill.allowed_tools is not None:
            restricted.append(skill)
    return restricted


def effective_tools_for_state(
    state: ConversationState,
    tools_map: dict[str, ToolDefinition],
) -> EffectiveTools:
    restricted_skills = _invoked_restricted_skills(state)
    if not restricted_skills:
        return EffectiveTools(tools_map=dict(tools_map))

    available_tool_names = set(tools_map)
    allowed_sets = [
        _normalize_allowed_tool_names(skill.allowed_tools or [], available_tool_names)
        for skill in restricted_skills
    ]
    allowed_names = set.intersection(*allowed_sets) if allowed_sets else set()
    allowed_names.update(ALWAYS_AVAILABLE_TOOL_NAMES & available_tool_names)

    return EffectiveTools(
        tools_map={
            name: tool for name, tool in tools_map.items() if name in allowed_names
        },
        restricted_skill_names=tuple(skill.name for skill in restricted_skills),
    )
