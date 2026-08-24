"""Factory spawn tool — route sub-work into the factory as child conversations.

This module defines the schema and tool class for spawning a factory child
conversation. It mirrors the wire payload proven by the openhands-factory-ctl
chassis (``build_start_payload``) so child conversations carry the factory tags
the canvas tree / run-graph / trace chips key off (``factory``/``runid``/
``workstreamid``).

Semantics:
- Same workspace as the caller -> child is linked via ``parent_conversation_id``
  (deep tree; the agent-server requires parent and child to share a workspace).
- Different ``target_workspace`` -> child is a NEW ROOT (no parent link); it
  still renders in the canvas tree as its own root and traces to Laminar.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.factory.impl import FactorySpawnExecutor


class FactorySpawnAction(Action):
    """Schema for spawning a factory child conversation."""

    prompt: str = Field(
        description="The sub-task for the child agent to perform.",
    )
    workstream_label: str | None = Field(
        default=None,
        description="Short label for the child workstream (e.g. ws-search).",
    )
    target_workspace: str | None = Field(
        default=None,
        description="Working directory for the child. Defaults to this "
        "conversation's workspace. If it differs, the child becomes a NEW "
        "ROOT (no parent link) because the agent-server requires parent and "
        "child to share a workspace.",
    )


class FactorySpawnObservation(Observation):
    """Observation from a factory spawn."""

    conversation_id: str = Field(description="The child conversation id.")
    workspace: str = Field(description="The child's working directory.")
    parent_conversation_id: str | None = Field(
        default=None,
        description="The parent conversation id, when the child is linked.",
    )
    run_id: str = Field(description="The factory run id the child belongs to.")

    def _get_factory_info(self) -> str:
        parent = self.parent_conversation_id or "none (new root)"
        return (
            f"Conversation ID: {self.conversation_id}\n"
            f"Workspace: {self.workspace}\n"
            f"Run ID: {self.run_id}\n"
            f"Parent: {parent}"
        )

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append(self._get_factory_info(), style="cyan")
        text.append("\n")
        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")
        text.append(self.text)
        return text


FACTORY_SPAWN_DESCRIPTION: Final[
    str
] = """Spawn a child conversation for a sub-task and link it as a child of this conversation (factory fan-out).

The child is a real, independent conversation that executes its own prompt. It
is linked as a parent-child node (visible in the canvas tree view and run-graph)
and traces to Laminar under the same factory run.

Use this tool when the work decomposes into independent, parallel streams that
should run as separate agents: several independent features, isolated
experiments, or long-running async work you will collect later. Each spawn
returns the child conversation id.

Do NOT use it for linear, quick, single-threaded work — keep that in this
conversation. Only spawn children for work that genuinely benefits from a
separate agent.
"""


class FactorySpawnTool(ToolDefinition[FactorySpawnAction, FactorySpawnObservation]):
    """Tool for spawning a factory child conversation."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",  # noqa: ARG003
    ) -> Sequence["FactorySpawnTool"]:
        from openhands.tools.factory.impl import FactorySpawnExecutor

        return [
            cls(
                action_type=FactorySpawnAction,
                observation_type=FactorySpawnObservation,
                description=FACTORY_SPAWN_DESCRIPTION,
                annotations=ToolAnnotations(
                    title="factory_spawn",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=FactorySpawnExecutor(),
            )
        ]


register_tool(FactorySpawnTool.name, FactorySpawnTool)
