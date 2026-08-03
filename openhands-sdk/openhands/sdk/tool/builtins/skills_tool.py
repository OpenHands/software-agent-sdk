from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import AliasChoices, Field

from openhands.sdk.skills import SkillRuntime
from openhands.sdk.tool.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


class SkillsListAction(Action):
    query: str | None = Field(default=None)
    limit: int = Field(default=5, ge=1, le=20)


class SkillsListObservation(Observation):
    skills: list[str] = Field(default_factory=list)


class SkillsReadAction(Action):
    skill_name: str = Field(
        validation_alias=AliasChoices("skill_name", "name"),
        description="Skill name. The legacy input key `name` is also accepted.",
    )
    path: str = Field(default="SKILL.md")


class SkillsReadObservation(Observation):
    skill_name: str = Field()
    path: str = Field()
    contents: str = Field()


def _validate_readable_skill_path(path: str) -> str | None:
    normalized = path.strip().replace("\\", "/")
    if normalized in ("", ".", "SKILL.md"):
        return "SKILL.md"
    parts = [part for part in normalized.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None
    if not parts:
        return "SKILL.md"
    if parts[0] in {"references", "assets"}:
        return "/".join(parts)
    return None


def _runtime_from_conversation(conversation) -> SkillRuntime:
    if conversation is None:
        return SkillRuntime([])
    context = conversation.state.agent.agent_context
    return SkillRuntime(list(context.skills) if context else [])


class SkillsListExecutor(ToolExecutor):
    def __call__(
        self, action: SkillsListAction, conversation=None
    ) -> SkillsListObservation:
        runtime = _runtime_from_conversation(conversation)
        if action.query:
            selection = runtime.select(action.query, limit=action.limit)
            skills = [entry.name for entry in selection.candidate_entries]
        else:
            skills = [entry.name for entry in runtime.list()]
        return SkillsListObservation.from_text(
            text="\n".join(skills) if skills else "No skills found.",
            skills=skills,
        )


class SkillsReadExecutor(ToolExecutor):
    def __call__(
        self, action: SkillsReadAction, conversation=None
    ) -> SkillsReadObservation:
        readable_path = _validate_readable_skill_path(action.path)
        if readable_path is None:
            return SkillsReadObservation.from_text(
                text=(
                    "Skill scripts are executable helpers, not reference "
                    "documents. Follow the skill's SKILL.md instructions and "
                    "run the named script or platform tool instead of reading "
                    "scripts/ source files."
                ),
                is_error=True,
                skill_name=action.skill_name,
                path=action.path,
                contents="",
            )
        runtime = _runtime_from_conversation(conversation)
        result = runtime.read(action.skill_name, readable_path)
        return SkillsReadObservation.from_text(
            text=result.handle.contents,
            skill_name=result.entry.name,
            path=result.handle.relative_path,
            contents=result.handle.contents,
        )


class SkillsListTool(ToolDefinition[SkillsListAction, SkillsListObservation]):
    def declared_resources(self, action: Action) -> DeclaredResources:  # noqa: ARG002
        return DeclaredResources(keys=(), declared=True)

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:  # noqa: ARG003
        if params:
            raise ValueError("SkillsListTool doesn't accept parameters")
        return [
            cls(
                action_type=SkillsListAction,
                observation_type=SkillsListObservation,
                description="List available skills, optionally filtering by query.",
                executor=SkillsListExecutor(),
                annotations=ToolAnnotations(
                    title="skills_list",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


class SkillsReadTool(ToolDefinition[SkillsReadAction, SkillsReadObservation]):
    def declared_resources(self, action: Action) -> DeclaredResources:
        path = getattr(action, "path", "SKILL.md")
        skill_name = getattr(action, "skill_name", "")
        return DeclaredResources(
            keys=(f"skill:{skill_name}:{path}",),
            declared=True,
        )

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:  # noqa: ARG003
        if params:
            raise ValueError("SkillsReadTool doesn't accept parameters")
        return [
            cls(
                action_type=SkillsReadAction,
                observation_type=SkillsReadObservation,
                description=(
                    "Read a skill document or bundled resource by skill name and "
                    "relative path."
                ),
                executor=SkillsReadExecutor(),
                annotations=ToolAnnotations(
                    title="skills_read",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
