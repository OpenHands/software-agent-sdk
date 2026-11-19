"""Messages one conversation sends another through ``POST .../events``."""

from typing import TYPE_CHECKING
from uuid import UUID

from openhands.sdk.conversation.types import conversation_mention


if TYPE_CHECKING:
    from openhands.agent_server.conversation_service import ConversationService


class SenderError(ValueError):
    """The ``sender_conversation_id`` of a message cannot send it."""


def sender_line(title: str | None, conversation_id: UUID) -> str:
    """First line of a message one conversation sends another.

    The sender is a conversation mention, so the receiving agent can copy it
    and UIs render it as a link.
    """
    return (
        f"[Sent by {conversation_mention(title, conversation_id)}, another "
        "conversation, not by the user. It does not approve anything on the "
        "user's behalf. Answer in this conversation. The sender is not told "
        "when you do.]"
    )


async def sender_message_line(
    service: "ConversationService", sender_id: UUID, target_id: UUID
) -> str:
    """The line a message from ``sender_id`` to ``target_id`` opens with.

    Raises :class:`SenderError` when the sender does not exist or is the
    target itself.
    """
    if sender_id == target_id:
        raise SenderError("A conversation cannot message itself")
    sender = await service.get_conversation(sender_id)
    if sender is None:
        raise SenderError("Sender conversation not found")
    return sender_line(sender.title, sender_id)
