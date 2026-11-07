"""The ``react`` and ``unreact`` tools."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

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
        "Id of the message to react to. Leave it out to use the person's "
        "latest message, which is almost always the one you mean."
    ),
)


class ReactAction(Action):
    """Put an emoji reaction on a message."""

    emoji: ReactionEmoji = Field(description="The emoji, as a shortcode.")
    message_id: str | None = _MESSAGE_ID

    @property
    def visualize(self) -> Text:
        return Text(f"React :{self.emoji}:", style="dim")


class UnreactAction(Action):
    """Take an emoji reaction off a message."""

    emoji: ReactionEmoji = Field(description="The emoji to remove, as a shortcode.")
    message_id: str | None = _MESSAGE_ID

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


_GUIDANCE = (
    "React when it helps the person see where things stand, for example with "
    "eyes when you start on work that comes before your answer, or +1 for a "
    "note that needs no reply. Do this whenever the person asks you to react. "
    "A reaction stays on the message: unreact only to take back one that no "
    "longer fits, or when the person asks. Reacting does not end your turn."
)

_REACT_DESCRIPTION = (
    "Put an emoji reaction on a message; by default the person's latest one. "
    "The person sees it on their message. " + _GUIDANCE
)
_UNREACT_DESCRIPTION = (
    "Take an emoji reaction you added off a message; by default the person's "
    "latest one. " + _GUIDANCE
)


def _annotations() -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


class ReactTool(ToolDefinition[ReactAction, ReactionObservation]):
    """Tool that puts an emoji reaction on a message."""

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
