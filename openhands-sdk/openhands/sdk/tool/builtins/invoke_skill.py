from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.skills.catalog import SkillCatalog, SkillCatalogEntry
from openhands.sdk.skills.execute import render_content_with_commands
from openhands.sdk.tool.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from openhands.sdk.utils.path import to_posix_path


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


class InvokeSkillAction(Action):
    name: str = Field(description="Name of the loaded skill to invoke.")

    @property
    def visualize(self) -> Text:
        t = Text()
        t.append("Invoke skill: ", style="bold blue")
        t.append(self.name)
        return t


class InvokeSkillObservation(Observation):
    skill_name: str = Field(
        description="Name of the skill this observation corresponds to."
    )

    @property
    def visualize(self) -> Text:
        t = Text()
        t.append(f"[skill: {self.skill_name}]\n", style="bold green")
        t.append(self.text)
        return t


TOOL_DESCRIPTION = """Invoke a skill by name.

This is the only supported way to invoke a skill listed in
`<available_skills>`. Call it with the `<name>` shown in that block; the
skill's full content is rendered (including any dynamic context) and
returned as the tool result.
"""


class InvokeSkillExecutor(ToolExecutor):
    @staticmethod
    def _get_catalog_and_working_dir(
        conversation: BaseConversation | None,
    ) -> tuple[SkillCatalog, Path | None]:
        if conversation is None:
            return SkillCatalog(), None

        state = conversation.state
        ctx = state.agent.agent_context
        skills = list(ctx.skills) if ctx else []
        catalog = SkillCatalog()
        for skill in skills:
            catalog.add(
                SkillCatalogEntry(
                    name=skill.name,
                    skill=skill,
                    display_path=skill.source,
                    prompt_visible=not skill.disable_model_invocation,
                )
            )
        working_dir = state.workspace.working_dir
        return catalog, Path(working_dir) if working_dir else None

    @staticmethod
    def _record_invocation(conversation: BaseConversation | None, name: str) -> None:
        if conversation is None:
            return
        invoked = conversation.state.invoked_skills
        if name not in invoked:
            invoked.append(name)

    @staticmethod
    def _error(name: str, text: str) -> InvokeSkillObservation:
        return InvokeSkillObservation.from_text(
            text=text, is_error=True, skill_name=name
        )

    def __call__(
        self,
        action: InvokeSkillAction,
        conversation: BaseConversation | None = None,
    ) -> InvokeSkillObservation:
        catalog, working_dir = self._get_catalog_and_working_dir(conversation)
        name = action.name.strip()

        match = catalog.get(name)
        if match is None:
            available = ", ".join(sorted(s.name for s in catalog.enabled())) or "<none>"
            return self._error(
                name, f"Unknown skill '{name}'. Available skills: {available}."
            )
        if not match.prompt_visible:
            return self._error(
                name,
                (
                    f"Skill '{name}' cannot be invoked directly. "
                    "It can only be activated by trigger matching."
                ),
            )

        rendered = render_content_with_commands(
            match.main_prompt, working_dir=working_dir
        )
        rendered = self._append_skill_location_footer(
            rendered, match.source, working_dir
        )
        self._record_invocation(conversation, name)
        return InvokeSkillObservation.from_text(text=rendered, skill_name=name)

    @staticmethod
    def _append_skill_location_footer(
        rendered: str, source: str | None, working_dir: Path | None
    ) -> str:
        if not source:
            return rendered
        try:
            skill_md = Path(source).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return rendered
        if not skill_md.is_file():
            return rendered
        skill_dir = skill_md.parent
        display: Path = skill_dir
        if working_dir is not None:
            try:
                display = skill_dir.relative_to(working_dir.resolve())
            except (ValueError, OSError):
                pass
        footer = (
            f"\n\n---\n"
            f"This skill is located at `{to_posix_path(display)}`. "
            f"Any files it references (e.g. under `scripts/`, `references/`, "
            f"`assets/`) are relative to that directory."
        )
        return rendered + footer


class InvokeSkillTool(ToolDefinition[InvokeSkillAction, InvokeSkillObservation]):
    """Built-in tool for explicit invocation of progressive-disclosure skills."""

    def declared_resources(self, action: Action) -> DeclaredResources:
        name = getattr(action, "name", "") or ""
        return DeclaredResources(keys=(f"skill:{name.strip()}",), declared=True)

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("InvokeSkillTool doesn't accept parameters")
        return [
            cls(
                action_type=InvokeSkillAction,
                observation_type=InvokeSkillObservation,
                description=TOOL_DESCRIPTION,
                executor=InvokeSkillExecutor(),
                annotations=ToolAnnotations(
                    title="invoke_skill",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
