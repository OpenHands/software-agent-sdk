"""Action, observation, and tool definitions for a project's coordinator.

A project coordinator starts conversations with ``start_child_conversation`` (a child
copies the coordinator's ``project`` tag, so it joins the project). These two
tools cover the rest of the loop: see every conversation of the project and what it
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


class ProjectConversationSummary(BaseModel):
    """One conversation of the project, as the conversations endpoint reports it."""

    conversation_id: UnmaskedID
    title: str | None = None
    execution_status: str
    attention: str
    resolved: str | None = None
    turn: str | None = None
    automation_id: str | None = None
    parent_conversation_id: UnmaskedID | None = None
    updated_at: str | None = None


# list_project_conversations


class ListProjectConversationsAction(Action):
    """List the other conversations in this conversation's project."""

    @property
    def visualize(self) -> Text:
        return Text("List project conversations", style="bold cyan")


class ListProjectConversationsObservation(Observation):
    project_id: UnmaskedID | None = None
    conversations: list[ProjectConversationSummary] = Field(default_factory=list)

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Listing project conversations failed", style="bold red")
            content.append("\n")
            content.append(self.text)
            return content
        content.append(
            f"{len(self.conversations)} project conversations", style="bold green"
        )
        for conversation in self.conversations:
            content.append(f"\n[{conversation.attention}] ")
            content.append(conversation.title or conversation.conversation_id)
            content.append(f" ({conversation.execution_status})", style="dim")
        return content


COORDINATOR_GUIDANCE = (
    "If you coordinate the project, delegate by default: start a new project "
    "conversation (start_child_conversation) for each new piece of work, and "
    "send a follow-up (message_project_conversation) to the conversation that "
    "already owns a task when the person adds to it, instead of doing the work "
    "yourself. When a conversation finishes a turn you asked for, you receive "
    "its final message as an update; tell the person what they need from it, "
    "and read more with read_project_conversation only when you need it. "
    "Turns the person starts in a conversation are theirs to follow."
)

_LIST_DESCRIPTION = (
    "List the conversations of the project this conversation coordinates or belongs "
    "to: every other conversation tagged with the same project, including the "
    "ones you started with start_child_conversation.\n\n"
    + COORDINATOR_GUIDANCE
    + "\n\n"
    "Conversations come grouped by `attention`: `needs_you` (waiting for "
    "confirmation, paused, error or stuck), `needs_input` (finished with a "
    "question for the person), `working` (running), `finished`, `idle` and "
    "`resolved` (the person or a confident critic marked it done). "
    "`finished` means the run ended; it does not say the task succeeded, so "
    "read the conversation before reporting an outcome. Call this again to refresh; "
    "a previous result is not the current state."
)


class ListProjectConversationsTool(
    ToolDefinition[ListProjectConversationsAction, ListProjectConversationsObservation]
):
    """Tool that lists the conversations of the caller's project."""

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
                "ListProjectConversationsTool does not accept parameters: "
                f"{sorted(params)}"
            )
        from openhands.tools.project.impl import ListProjectConversationsExecutor

        return [
            cls(
                description=_LIST_DESCRIPTION,
                action_type=ListProjectConversationsAction,
                observation_type=ListProjectConversationsObservation,
                executor=ListProjectConversationsExecutor(conv_state, server_url),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


# message_project_conversation


class MessageProjectConversationAction(Action):
    """Send a follow-up message to another conversation in this project."""

    conversation_id: str = Field(
        description=(
            "Id of the conversation to message, exactly as "
            "list_project_conversations or start_child_conversation returned it."
        )
    )
    message: str = Field(
        min_length=1,
        description=(
            "Self-contained message for the conversation. It cannot see this "
            "conversation, so include the context it needs."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Message project conversation", style="bold cyan")
        content.append(f" {self.conversation_id}\n")
        content.append(self.message)
        return content


class MessageProjectConversationObservation(Observation):
    conversation_id: UnmaskedID | None = None
    previous_status: str | None = None
    run: str | None = Field(
        default=None,
        description=(
            "started, queued, waiting_for_confirmation or not_run, as the "
            "server reported it."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Message to project conversation failed", style="bold red")
            content.append("\n")
            content.append(self.text)
            return content
        content.append("Message delivered", style="bold green")
        if self.conversation_id:
            content.append(f" to {self.conversation_id}")
        return content


_MESSAGE_DESCRIPTION = (
    "Send a follow-up message to another conversation in your project and "
    "ask it to run: to correct, resume or extend its task. Only conversations that "
    "list_project_conversations reports can be messaged; start a new conversation with "
    "start_child_conversation for a distinct task.\n\n"
    "A running conversation receives the message in its current run; a paused, "
    "finished or idle conversation is run to handle it. A conversation waiting for the "
    "person to approve an action is never run by this tool, since that would "
    "approve the action: it is refused, and you should tell the person what "
    "it waits on. A successful result means the message was saved and a run "
    "was requested, not that the conversation acted on it; check "
    "list_project_conversations or read the conversation before reporting "
    "progress. After an error that says the message may still be delivered, "
    "do not resend it."
)


class MessageProjectConversationTool(
    ToolDefinition[
        MessageProjectConversationAction, MessageProjectConversationObservation
    ]
):
    """Tool that sends a follow-up message to a conversation of the caller's project."""

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
                tag names the project whose conversations it may message.
            server_url: Optional agent-server base URL. Defaults to the
                agent-server running this tool.
        """
        if params:
            raise ValueError(
                "MessageProjectConversationTool does not accept parameters: "
                f"{sorted(params)}"
            )
        from openhands.tools.project.impl import MessageProjectConversationExecutor

        return [
            cls(
                description=_MESSAGE_DESCRIPTION,
                action_type=MessageProjectConversationAction,
                observation_type=MessageProjectConversationObservation,
                executor=MessageProjectConversationExecutor(conv_state, server_url),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


# save_project_artifact


class SaveProjectArtifactAction(Action):
    """Save a file from this conversation's workspace as a project artifact."""

    path: str = Field(
        description=(
            "The file to save, relative to the working directory or absolute inside it."
        )
    )
    name: str | None = Field(
        default=None,
        description=(
            "Name to save it under; the file's own name when left out. A flat "
            "file name of letters, digits, spaces and ._()+-. An artifact of "
            "the same name is replaced."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Save project artifact", style="bold cyan")
        content.append(f" {self.path}")
        if self.name:
            content.append(f" as {self.name}")
        return content


class SaveProjectArtifactObservation(Observation):
    project_id: UnmaskedID | None = None
    name: str | None = None
    size: int | None = None

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Saving project artifact failed", style="bold red")
            content.append("\n")
            content.append(self.text)
            return content
        content.append("Artifact saved", style="bold green")
        if self.name:
            content.append(f": {self.name}")
        return content


_SAVE_ARTIFACT_DESCRIPTION = (
    "Save a file you made as an artifact of this conversation's project, so "
    "the person finds it on the project board. Use it for deliverables that "
    "do not belong in a source repository: reports, write-ups, images, "
    "exports, data files. Do not use it for files that are committed to a "
    "repository. The file is copied, so save it again after changing it."
)


class SaveProjectArtifactTool(
    ToolDefinition[SaveProjectArtifactAction, SaveProjectArtifactObservation]
):
    """Tool that copies a workspace file into the caller's project artifacts."""

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
                tag names the project the artifact is saved to.
            server_url: Optional agent-server base URL. Defaults to the
                agent-server running this tool.
        """
        if params:
            raise ValueError(
                f"SaveProjectArtifactTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.project.impl import SaveProjectArtifactExecutor

        return [
            cls(
                description=_SAVE_ARTIFACT_DESCRIPTION,
                action_type=SaveProjectArtifactAction,
                observation_type=SaveProjectArtifactObservation,
                executor=SaveProjectArtifactExecutor(conv_state, server_url),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


# read_project_conversation


class ReadProjectConversationAction(Action):
    """Read the latest messages of one conversation of this project."""

    conversation_id: str = Field(
        description=(
            "Id of the conversation to read, as list_project_conversations returned it."
        )
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="How many recent messages to read."
    )

    @property
    def visualize(self) -> Text:
        return Text(
            f"Read project conversation {self.conversation_id}", style="bold cyan"
        )


class ReadProjectConversationObservation(Observation):
    conversation_id: UnmaskedID | None = None

    @property
    def visualize(self) -> Text:
        return Text(self.text, style="red" if self.is_error else "")


_READ_DESCRIPTION = (
    "Read the latest messages of one conversation of your project: what the "
    "person, the agent and other conversations said, and the agent's finish "
    "message, oldest first, without its tool calls. Use it to catch up on a "
    "conversation before you report on it or send it a follow-up; the updates "
    "you receive only carry each turn's final message."
)


class ReadProjectConversationTool(
    ToolDefinition[ReadProjectConversationAction, ReadProjectConversationObservation]
):
    """Tool that reads the recent messages of a project conversation."""

    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        server_url: str | None = None,
        **params,
    ) -> Sequence[Self]:
        """Create the tool bound to the current conversation."""
        if params:
            raise ValueError(
                "ReadProjectConversationTool does not accept parameters: "
                f"{sorted(params)}"
            )
        from openhands.tools.project.impl import ReadProjectConversationExecutor

        return [
            cls(
                description=_READ_DESCRIPTION,
                action_type=ReadProjectConversationAction,
                observation_type=ReadProjectConversationObservation,
                executor=ReadProjectConversationExecutor(conv_state, server_url),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(ListProjectConversationsTool.name, ListProjectConversationsTool)
register_tool(ReadProjectConversationTool.name, ReadProjectConversationTool)
register_tool(SaveProjectArtifactTool.name, SaveProjectArtifactTool)
register_tool(MessageProjectConversationTool.name, MessageProjectConversationTool)
