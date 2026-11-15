"""Executor for the ``edit_message`` tool."""

from typing import TYPE_CHECKING

from openhands.sdk.event.message_edit import find_edit
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.message_edit.definition import (
    EditMessageAction,
    EditMessageObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


class EditMessageExecutor(ToolExecutor[EditMessageAction, EditMessageObservation]):
    """Find the message and return the edit.

    The executor only reads the conversation; the agent records the
    :class:`MessageEditEvent` from the observation on its own thread, so this
    is safe when tools run in parallel.
    """

    def __call__(
        self,
        action: EditMessageAction,
        conversation: "LocalConversation | None" = None,
    ) -> EditMessageObservation:
        if conversation is None:
            return EditMessageObservation.from_text(
                "Editing a message needs a conversation.", is_error=True
            )
        try:
            edit = find_edit(
                conversation.state.active_branch(), action.old_str, action.new_str
            )
        except ValueError as exc:
            return EditMessageObservation.from_text(str(exc), is_error=True)
        return EditMessageObservation.from_text(
            "Edited. The message now reads:\n" + edit.text,
            target_event_id=edit.target_event_id,
            edited_text=edit.text,
        )
