"""The ``react`` and ``unreact`` tools."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.event import Event, EventID, ReactionEmoji, ReactionEvent
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


_MESSAGE_ID = Field(
    default=None,
    description=(
        "Id of the message to react to. Leave it out to use the user's "
        "latest message, which is almost always the one you mean."
    ),
)


class ReactAction(Action):
    """Put an emoji reaction on a message."""

    emoji: ReactionEmoji = Field(description="The emoji, as a shortcode.")
    message_id: EventID | None = _MESSAGE_ID

    @property
    def visualize(self) -> Text:
        return Text(f"React :{self.emoji}:", style="dim")


class UnreactAction(Action):
    """Take an emoji reaction off a message."""

    emoji: ReactionEmoji = Field(description="The emoji to remove, as a shortcode.")
    message_id: EventID | None = _MESSAGE_ID

    @property
    def visualize(self) -> Text:
        return Text(f"Unreact :{self.emoji}:", style="dim")


class ReactionObservation(Observation):
    """The result of ``react`` or ``unreact``.

    On success it carries the reaction, which the agent records as a
    :class:`ReactionEvent` right after this observation.
    """

    target_event_id: EventID | None = None
    emoji: ReactionEmoji | None = None
    removed: bool = False

    def follow_up_events(self) -> Sequence[Event]:
        if self.is_error or self.target_event_id is None or self.emoji is None:
            return ()
        return (
            ReactionEvent(
                source="agent",
                target_event_id=self.target_event_id,
                emoji=self.emoji,
                removed=self.removed,
            ),
        )

    @property
    def visualize(self) -> Text:
        if self.is_error:
            return Text(self.text, style="red")
        return Text()


_REACT_DESCRIPTION = """\
Put an emoji reaction on a message; by default the user's latest one.
The user sees it on their message right away, while you keep working, so a
reaction is the smallest sign that you got a message and what you make of it.

Use the reaction that fits:
- +1 when the user corrects you, changes the plan or adds to it while you
  are working ("wait, actually...", "also...", "use X instead"): react +1
  first, then carry on with the change. No need to stop and reply. Also +1 a
  note or go-ahead that needs no reply.
- eyes when a message needs work before you can answer it: react when you
  start, and answer when you have the result.
- white_check_mark when you have done what a message asked and there is
  nothing more to say about it.
- thinking_face when a message raises something you need to look into
  before you can agree with it.
- tada when something the user cares about lands: a fix works, a release
  is out, good news.
- heart when the user thanks you or shares something kind.

A reaction never replaces an answer: a question still gets a reply. React
when it tells the user something, not on every message, and whenever the
user asks you to. A reaction stays on the message. Reacting does not end
your turn."""

_UNREACT_DESCRIPTION = """\
Take an emoji reaction you added off a message; by default the user's
latest one. Do this only when it no longer fits (for example eyes on work you
decided not to do) or when the user asks: a reaction is meant to stay."""


def _annotations() -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        # Each call records a new ReactionEvent.
        idempotentHint=False,
        openWorldHint=False,
    )


class ReactTool(ToolDefinition[ReactAction, ReactionObservation]):
    """Tool that puts an emoji reaction on a message."""

    # Given by the conversation_reactions feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(f"ReactTool does not accept parameters: {sorted(params)}")
        from openhands.tools.reaction.impl import ReactionExecutor

        return [
            cls(
                description=_REACT_DESCRIPTION,
                action_type=ReactAction,
                observation_type=ReactionObservation,
                executor=ReactionExecutor(),
                annotations=_annotations(),
            )
        ]


class UnreactTool(ToolDefinition[UnreactAction, ReactionObservation]):
    """Tool that takes an emoji reaction off a message."""

    # Given by the conversation_reactions feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                f"UnreactTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.reaction.impl import ReactionExecutor

        return [
            cls(
                description=_UNREACT_DESCRIPTION,
                action_type=UnreactAction,
                observation_type=ReactionObservation,
                executor=ReactionExecutor(),
                annotations=_annotations(),
            )
        ]


register_tool(ReactTool.name, ReactTool)
register_tool(UnreactTool.name, UnreactTool)
