from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.skills.execute import render_content_with_commands
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
    def _get_skills_and_working_dir(
        conversation: BaseConversation | None,
    ) -> tuple[list, Path | None]:
        if conversation is None:
            return [], None

        agent = getattr(conversation, "agent", None)
        ctx = getattr(agent, "agent_context", None)
        skills = list(ctx.skills) if ctx else []

        ws = getattr(conversation, "workspace", None)
        wd = getattr(ws, "working_dir", None)
        return skills, Path(wd) if wd else None

    @staticmethod
    def _record_invocation(conversation: BaseConversation | None, name: str) -> None:
        if conversation is None:
            return
        state = getattr(conversation, "_state", None)
        if state is None:
            state = getattr(conversation, "state", None)
        invoked = getattr(state, "invoked_skills", None)
        if isinstance(invoked, list) and name not in invoked:
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

        match = next((s for s in skills if s.name == name), None)
        if match is None:
            available = ", ".join(sorted(s.name for s in skills)) or "<none>"
            return self._error(
                name, f"Unknown skill '{name}'. Available skills: {available}."
            )

        rendered = render_content_with_commands(match.content, working_dir=working_dir)
        self._record_invocation(conversation, name)
        return InvokeSkillObservation.from_text(text=rendered, skill_name=name)


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
