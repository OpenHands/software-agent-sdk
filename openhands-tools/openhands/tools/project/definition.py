"""Action, observation, and tool definitions for a project's coordinator.

A project coordinator starts threads with ``start_child_conversation`` (a child
copies the coordinator's ``project`` tag, so it joins the project). These two
tools cover the rest of the loop: see every thread of the project and what it
needs, and send one of them a follow-up.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import BaseModel, Field
from rich.text import Text

from openhands.sdk.event.types import UnmaskedID
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class ProjectThreadSummary(BaseModel):
    """One conversation of the project, as the threads endpoint reports it."""

    conversation_id: UnmaskedID
    title: str | None = None
    execution_status: str
    attention: str
    parent_conversation_id: UnmaskedID | None = None
    updated_at: str | None = None


# list_project_threads


class ListProjectThreadsAction(Action):
    """List the threads of this conversation's project."""

    @property
    def visualize(self) -> Text:
        return Text("List project threads", style="bold cyan")


class ListProjectThreadsObservation(Observation):
    project_id: UnmaskedID | None = None
    threads: list[ProjectThreadSummary] = Field(default_factory=list)

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Listing project threads failed", style="bold red")
            content.append("\n")
            content.append(self.text)
            return content
        content.append(f"{len(self.threads)} project threads", style="bold green")
        for thread in self.threads:
            content.append(f"\n[{thread.attention}] ")
            content.append(thread.title or thread.conversation_id)
            content.append(f" ({thread.execution_status})", style="dim")
        return content


_LIST_DESCRIPTION = (
    "List the threads of the project this conversation coordinates or belongs "
    "to: every other conversation tagged with the same project, including the "
    "ones you started with start_child_conversation.\n\n"
    "Threads come grouped by `attention`, which is derived from the thread's "
    "execution status: `needs_you` (waiting for confirmation, paused, error or "
    "stuck), `working` (running), `finished` and `idle`. `finished` means the "
    "run ended; it does not say the task succeeded, so read the thread before "
    "reporting an outcome. Call this again to refresh; a previous result is "
    "not the current state."
)


class ListProjectThreadsTool(
    ToolDefinition[ListProjectThreadsAction, ListProjectThreadsObservation]
):
    """Tool that lists the threads of the caller's project."""

    # Attached by the host that runs the projects API, not picked from the
    # catalog.
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        server_url: str | None = None,
        **params,
    ) -> Sequence[Self]:
        """Create the tool bound to the current conversation.

        Args:
            conv_state: State of the calling conversation; its ``project``
                tag names the project.
            server_url: Optional agent-server base URL. Defaults to the
                agent-server running this tool.
        """
        if params:
            raise ValueError(
                f"ListProjectThreadsTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.project.impl import ListProjectThreadsExecutor

        return [
            cls(
                description=_LIST_DESCRIPTION,
                action_type=ListProjectThreadsAction,
                observation_type=ListProjectThreadsObservation,
                executor=ListProjectThreadsExecutor(conv_state, server_url),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


# message_project_thread


class MessageProjectThreadAction(Action):
    """Send a follow-up message to one thread of this conversation's project."""

    conversation_id: str = Field(
        description=(
            "Id of the thread to message, exactly as list_project_threads or "
            "start_child_conversation returned it."
        )
    )
    message: str = Field(
        min_length=1,
        description=(
            "Self-contained message for the thread. It cannot see this "
            "conversation, so include the context it needs."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Message project thread", style="bold cyan")
        content.append(f" {self.conversation_id}\n")
        content.append(self.message)
        return content


class MessageProjectThreadObservation(Observation):
    conversation_id: UnmaskedID | None = None
    previous_status: str | None = None

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Message to project thread failed", style="bold red")
            content.append("\n")
            content.append(self.text)
            return content
        content.append("Message delivered", style="bold green")
        if self.conversation_id:
            content.append(f" to {self.conversation_id}")
        return content


_MESSAGE_DESCRIPTION = (
    "Send a follow-up message to a thread of this conversation's project and "
    "ask it to run: to correct, resume or extend its task. Only threads that "
    "list_project_threads reports can be messaged; start a new thread with "
    "start_child_conversation for a distinct task.\n\n"
    "A running thread receives the message in its current run. A thread "
    "waiting for confirmation keeps waiting: the message is saved, but the "
    "person must still approve or reject the pending action. A successful "
    "result means the message was saved and a run was requested, not that the "
    "thread acted on it; check list_project_threads or read the thread before "
    "reporting progress. Do not resend after an error that may have been a "
    "timeout without checking first."
)


class MessageProjectThreadTool(
    ToolDefinition[MessageProjectThreadAction, MessageProjectThreadObservation]
):
    """Tool that sends a follow-up message to a thread of the caller's project."""

    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        server_url: str | None = None,
        **params,
    ) -> Sequence[Self]:
        """Create the tool bound to the current conversation.

        Args:
            conv_state: State of the calling conversation; its ``project``
                tag names the project whose threads it may message.
            server_url: Optional agent-server base URL. Defaults to the
                agent-server running this tool.
        """
        if params:
            raise ValueError(
                f"MessageProjectThreadTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.project.impl import MessageProjectThreadExecutor

        return [
            cls(
                description=_MESSAGE_DESCRIPTION,
                action_type=MessageProjectThreadAction,
                observation_type=MessageProjectThreadObservation,
                executor=MessageProjectThreadExecutor(conv_state, server_url),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(ListProjectThreadsTool.name, ListProjectThreadsTool)
register_tool(MessageProjectThreadTool.name, MessageProjectThreadTool)
