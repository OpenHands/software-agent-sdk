from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.skills.execute import render_content_with_commands
from openhands.sdk.skills.fork import run_skill_forked
from openhands.sdk.tool.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.skills.skill import Skill


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
        text = Text()
        text.append(f"Skill: {self.skill_name}", style="blue")
        text.append("\n")
        text.append(self.text)
        return text


TOOL_DESCRIPTION = """Invoke a skill by name.

This is the only supported way to invoke a skill listed in
`<available_skills>`. Call it with the `<name>` shown in that block; the
skill's full content is rendered (including any dynamic context) and
returned as the tool result.
"""


class InvokeSkillExecutor(ToolExecutor):
    @staticmethod
    def _get_skills_and_working_dir(
        conversation: BaseConversation | None,
    ) -> tuple[list, Path | None]:
        """Extract the skill catalog and working dir from the conversation state."""
        if conversation is None:
            return [], None

        state = conversation.state
        ctx = state.agent.agent_context
        skills = list(ctx.skills) if ctx else []
        working_dir = state.workspace.working_dir
        return skills, Path(working_dir) if working_dir else None

    @staticmethod
    def _record_invocation(conversation: BaseConversation | None, name: str) -> None:
        """Append `name` to the conversation's invoked-skills list (deduped)."""
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
        skills, working_dir = self._get_skills_and_working_dir(conversation)
        name = action.name.strip()

        skill = next((s for s in skills if s.name == name), None)
        if skill is None:
            return self._unknown_skill_error(name, skills)

        # A resolved skill came from `skills`, which is only non-empty when
        # `conversation` is not None. Narrow for the helpers.
        assert conversation is not None

        if skill.context == "fork":
            return self._invoke_forked(skill, conversation, working_dir)
        return self._invoke_inline(skill, conversation, working_dir)

    def _unknown_skill_error(
        self, name: str, skills: list[Skill]
    ) -> InvokeSkillObservation:
        available = ", ".join(sorted(s.name for s in skills)) or "<none>"
        return self._error(
            name, f"Unknown skill '{name}'. Available skills: {available}."
        )

    def _invoke_forked(
        self,
        skill: Skill,
        conversation: BaseConversation,
        working_dir: Path | None,
    ) -> InvokeSkillObservation:
        # `conversation is None` is unreachable here: a None conversation yields
        # an empty skill catalog upstream, so lookup returns _unknown_skill_error
        # before we get here. The only real failure is a missing working_dir.
        if working_dir is None:
            return self._error(
                skill.name,
                f"Skill '{skill.name}' has context=fork but the conversation "
                "has no working_dir; forked skills require a workspace.",
            )
        text = run_skill_forked(
            skill,
            conversation.state.agent,
            working_dir,
            conversation.state.persistence_dir,
        )
        self._record_invocation(conversation, skill.name)
        return InvokeSkillObservation.from_text(text=text, skill_name=skill.name)

    def _invoke_inline(
        self,
        skill: Skill,
        conversation: BaseConversation,
        working_dir: Path | None,
    ) -> InvokeSkillObservation:
        rendered = render_content_with_commands(skill.content, working_dir=working_dir)
        rendered = self._append_skill_location_footer(
            rendered, skill.source, working_dir
        )
        self._record_invocation(conversation, skill.name)
        return InvokeSkillObservation.from_text(text=rendered, skill_name=skill.name)

    @staticmethod
    def _append_skill_location_footer(
        rendered: str, source: str | None, working_dir: Path | None
    ) -> str:
        """Append a trailing note pointing the LLM at the skill's on-disk directory.

        The AgentSkills spec allows skills to bundle `scripts/`, `references/`, and
        `assets/` alongside `SKILL.md`. Skill authors reference those by relative
        path, so the model needs to know where the skill lives to reach them.

        When the skill lives under the conversation's `working_dir`, the path is
        rendered relative to it to avoid leaking absolute home-directory paths
        into the LLM context.
        """
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
                pass  # skill lives outside working_dir, keep absolute
        footer = (
            f"\n\n---\n"
            f"This skill is located at `{display}`. "
            f"Any files it references (e.g. under `scripts/`, `references/`, "
            f"`assets/`) are relative to that directory."
        )
        return rendered + footer


class InvokeSkillTool(ToolDefinition[InvokeSkillAction, InvokeSkillObservation]):
    """Built-in tool for explicit invocation of progressive-disclosure skills."""

    def declared_resources(self, action: Action) -> DeclaredResources:
        # Rendering a skill may execute inline `!`cmd`` tokens, which can
        # touch arbitrary on-disk state. Keying on the skill name serializes
        # concurrent invocations of the same skill while still allowing
        # distinct skills to render in parallel.
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
