"""Executor for the ``react`` and ``unreact`` tools."""

from typing import TYPE_CHECKING

from openhands.sdk.event.reaction import find_reaction_target
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.reaction.definition import (
    ReactAction,
    ReactionObservation,
    UnreactAction,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


class ReactionExecutor(ToolExecutor[ReactAction | UnreactAction, ReactionObservation]):
    """Find the message and return the reaction.

    The executor only reads the conversation; the agent records the
    :class:`ReactionEvent` from the observation on its own thread, so this is
    safe when tools run in parallel.
    """

    def __call__(
        self,
        action: ReactAction | UnreactAction,
        conversation: "LocalConversation | None" = None,
    ) -> ReactionObservation:
        if conversation is None:
            return ReactionObservation.from_text(
                "Reactions need a conversation.", is_error=True
            )
        removed = isinstance(action, UnreactAction)
        message_id = (action.message_id or "").strip() or None
        try:
            target = find_reaction_target(
                conversation.state.active_branch(), message_id, source="agent"
            )
        except ValueError as exc:
            return ReactionObservation.from_text(str(exc), is_error=True)
        verb = "Removed" if removed else "Reacted"
        return ReactionObservation.from_text(
            f"{verb} :{action.emoji}:.",
            target_event_id=target.id,
            emoji=action.emoji,
            removed=removed,
        )
