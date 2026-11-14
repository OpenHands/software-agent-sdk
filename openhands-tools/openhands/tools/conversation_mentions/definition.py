"""The ``find_conversations`` and ``read_conversation`` tools."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self

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


class FindConversationsAction(Action):
    """Find the user's conversations by title."""

    query: str = Field(
        default="",
        description=(
            "Words to look for in the titles, in any case and order. Leave "
            "it empty to list the latest conversations."
        ),
    )
    limit: int = Field(
        default=10, ge=1, le=50, description="Most conversations to list."
    )

    @property
    def visualize(self) -> Text:
        query = self.query.strip()
        return Text(
            f"Find conversations: {query}" if query else "Find conversations",
            style="dim",
        )


class ReadConversationAction(Action):
    """Read the latest messages of another conversation."""

    conversation: str = Field(
        description=(
            "The conversation: its mention link ([Title](conversation:<id>)) or its id."
        )
    )
    limit: int = Field(default=20, ge=1, le=100, description="Most messages to read.")

    @property
    def visualize(self) -> Text:
        return Text(f"Read conversation {self.conversation}", style="dim")


class ConversationMentionsObservation(Observation):
    """What ``find_conversations`` or ``read_conversation`` found."""

    @property
    def visualize(self) -> Text:
        return Text(self.text, style="red" if self.is_error else "")


CONVERSATION_MENTIONS_GUIDANCE = """\
When you refer to another conversation, write its mention link,
[Title](conversation:<id>), with the conversation's title and id: the user
sees it as a link that opens that conversation. find_conversations gives the
link for each conversation it lists; for a conversation you started, use the
title and id you got back."""

_FIND_DESCRIPTION = """\
Find the user's other conversations on this server by title, newest activity
first. Each line gives the conversation's mention link, its status and when it
was last active. Use it when the user talks about another conversation ("the
one that fixed the export button") and you need to know which one, or to link
to it in your answer. It only reads titles; to see what a conversation said,
use read_conversation."""

_READ_DESCRIPTION = """\
Read the latest messages of another conversation: what the user and its agent
wrote, and the agent's final answers, oldest first, without tool calls. Give
it a mention link or an id, for example one the user put in their message.
It only reads: it never sends that conversation anything or runs it."""


def _read_only() -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


class FindConversationsTool(
    ToolDefinition[FindConversationsAction, ConversationMentionsObservation]
):
    """Tool that finds the user's conversations by title."""

    # Given by the conversation_mentions feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                f"FindConversationsTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.conversation_mentions.impl import (
            FindConversationsExecutor,
        )

        own_id = conv_state.id if conv_state is not None else None
        return [
            cls(
                description=_FIND_DESCRIPTION,
                action_type=FindConversationsAction,
                observation_type=ConversationMentionsObservation,
                executor=FindConversationsExecutor(own_id),
                annotations=_read_only(),
                prompt_guidance=CONVERSATION_MENTIONS_GUIDANCE,
            )
        ]


class ReadConversationTool(
    ToolDefinition[ReadConversationAction, ConversationMentionsObservation]
):
    """Tool that reads the latest messages of another conversation."""

    # Given by the conversation_mentions feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                f"ReadConversationTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.conversation_mentions.impl import (
            ReadConversationExecutor,
        )

        return [
            cls(
                description=_READ_DESCRIPTION,
                action_type=ReadConversationAction,
                observation_type=ConversationMentionsObservation,
                executor=ReadConversationExecutor(),
                annotations=_read_only(),
                prompt_guidance=CONVERSATION_MENTIONS_GUIDANCE,
            )
        ]


register_tool(FindConversationsTool.name, FindConversationsTool)
register_tool(ReadConversationTool.name, ReadConversationTool)
