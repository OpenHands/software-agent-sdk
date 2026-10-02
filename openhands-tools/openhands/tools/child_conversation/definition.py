"""Action, observation, and tool definitions for start_child_conversation."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class StartChildConversationAction(Action):
    """Action that starts a child conversation on the parent's backend."""

    task: str = Field(
        description=(
            "Self-contained brief for the child: the goal, relevant files or "
            "paths, constraints, and the expected deliverable. The child cannot "
            "see this conversation's history."
        )
    )
    title: str | None = Field(
        default=None, description="Optional short title for the child conversation."
    )
    isolation: Literal["worktree", "shared"] = Field(
        default="worktree",
        description=(
            "'worktree' gives the child its own git worktree and branch when the "
            "workspace is a git repository; 'shared' runs it in the same directory."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Start child conversation", style="bold cyan")
        if self.title:
            content.append(f": {self.title}")
        content.append(f" [{self.isolation}]\n")
        content.append(self.task)
        return content


class StartChildConversationObservation(Observation):
    """Result of a child conversation launch."""

    conversation_id: str | None = None
    parent_conversation_id: str | None = None
    status: str | None = None
    title: str | None = None
    url: str | None = None
    workspace: str | None = None
    isolation: Literal["worktree", "shared"] | None = None

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Child conversation launch failed", style="bold red")
            content.append("\n")
            content.append(self.text)
            return content
        content.append("Started child conversation", style="bold green")
        if self.title:
            content.append(f" '{self.title}'")
        if self.conversation_id:
            content.append(f" ({self.conversation_id})")
        if self.status:
            content.append(f" - {self.status}")
        if self.url:
            content.append("\n")
            content.append(self.url, style="underline")
        return content


_DESCRIPTION = (
    "Start a child conversation that runs on the same backend as this "
    "conversation, with its own agent and history, to work on a delegated task "
    "in parallel.\n\n"
    "The child inherits this conversation's configuration and workspace. `task` "
    "must be a self-contained brief: the child has no access to this "
    "conversation's history, so include the goal, relevant paths, constraints "
    "and the expected deliverable. Use `title` to name it. `isolation` controls "
    "the workspace: `worktree` (default) gives the child its own git worktree "
    "and branch when the workspace is a git repository, `shared` runs it in the "
    "same directory; backends that manage workspaces themselves may ignore this "
    "and report what was applied.\n\n"
    "The result contains the child's conversation id, its initial status and, "
    "when available, a URL. Report the id and URL to the user. A launch is not "
    "idempotent: call this once per task and do not retry a launch that already "
    "returned a conversation id."
)


class StartChildConversationTool(
    ToolDefinition[StartChildConversationAction, StartChildConversationObservation]
):
    """Tool that launches a same-backend child conversation."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        launch_url: str | None = None,
        **params,
    ) -> Sequence[Self]:
        """Create the tool bound to the current conversation.

        Args:
            conv_state: State of the conversation that becomes the parent.
            launch_url: Optional child-launch endpoint. Defaults to the
                agent-server running this tool; a hosting backend can point it
                at its own launcher.
        """
        if params:
            raise ValueError(
                "StartChildConversationTool does not accept parameters: "
                f"{sorted(params)}"
            )

        # Import here to keep module import light and avoid import cycles.
        from openhands.tools.child_conversation.impl import (
            StartChildConversationExecutor,
        )

        executor = StartChildConversationExecutor(
            parent_conversation_id=conv_state.id, launch_url=launch_url
        )
        return [
            cls(
                description=_DESCRIPTION,
                action_type=StartChildConversationAction,
                observation_type=StartChildConversationObservation,
                executor=executor,
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]


# Automatically register when this module is imported.
register_tool(StartChildConversationTool.name, StartChildConversationTool)
