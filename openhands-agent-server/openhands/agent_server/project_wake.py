"""Messages one conversation sends another, and the coordinator's wake-up.

:func:`sender_message_header` checks a ``sender_conversation_id`` given to
``POST /api/conversations/{id}/events`` and writes the line the message opens
with. :func:`notify_coordinator` wakes a project's coordinator when one of its
conversations finishes a run the coordinator asked for.

The coordinator gets the member's final message, not its events: one user
message that records the member as ``sender`` and opens with
:func:`~openhands.agent_server.project_prompts.update_header`. It is delivered
like a message sent with ``refuse_pending_confirmation``, so it never runs a
coordinator that is waiting for confirmation (the message is kept and seen on
its next run). The coordinator reads more with ``read_project_conversation``.
"""

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from openhands.agent_server.event_service import ConversationRunLimitExceeded
from openhands.agent_server.persistence import get_projects_store
from openhands.agent_server.project_board import TurnOutcome
from openhands.agent_server.project_prompts import (
    SenderRole,
    sender_header,
    update_header,
    update_text,
)
from openhands.sdk import Message, TextContent
from openhands.sdk.conversation.types import (
    CONVERSATION_SENDER_PREFIX,
    PROJECT_TAG_KEY,
    conversation_sender,
)
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.agent_server.conversation_service import ConversationService


logger = get_logger(__name__)


class SenderError(ValueError):
    """The ``sender_conversation_id`` of a message cannot send it."""


async def _coordinator_id(project_hex: str) -> UUID | None:
    try:
        persisted = await asyncio.to_thread(get_projects_store().load)
    except OSError:
        logger.warning("Projects read failed; sender named as a member")
        return None
    if persisted is None:
        return None
    project = next((p for p in persisted.projects if p.id.hex == project_hex), None)
    return project.coordinator_conversation_id if project else None


async def sender_message_header(
    service: "ConversationService",
    sender_id: UUID,
    target_id: UUID,
    target_tags: dict[str, str],
) -> str:
    """The line a message from ``sender_id`` to ``target_id`` opens with.

    Raises :class:`SenderError` when the sender does not exist, is the target
    itself, or is in a project the target is not in: a project's conversations
    only reach their own project.
    """
    if sender_id == target_id:
        raise SenderError("A conversation cannot message itself")
    sender = await service.get_conversation(sender_id)
    if sender is None:
        raise SenderError("Sender conversation not found")
    project_hex = sender.tags.get(PROJECT_TAG_KEY)
    role: SenderRole = "conversation"
    if project_hex:
        if target_tags.get(PROJECT_TAG_KEY) != project_hex:
            raise SenderError("Sender is not a conversation of this project")
        coordinator = await _coordinator_id(project_hex) == sender_id
        role = "coordinator" if coordinator else "member"
    return sender_header(sender.title, sender_id, role=role)


async def notify_coordinator(
    service: "ConversationService",
    member_id: UUID,
    project_hex: str,
    turn: TurnOutcome,
    text: str | None,
    turn_sender: str | None,
) -> bool:
    """Deliver a member's final message to its project's coordinator.

    Only a turn the coordinator started (its message, or the task message of
    a child it started) is delivered: the coordinator waits for that reply.
    A turn the person started in the member is theirs to follow, and an
    update the coordinator answered must not start another round.

    Returns whether a message was saved. Nothing is sent when the turn was
    not the coordinator's, when the project or its coordinator is gone, when
    the coordinator is the member itself, or when the coordinator left the
    project.
    """
    if turn_sender is None or not turn_sender.startswith(CONVERSATION_SENDER_PREFIX):
        return False
    persisted = await asyncio.to_thread(get_projects_store().load)
    if persisted is None:
        return False
    project = next((p for p in persisted.projects if p.id.hex == project_hex), None)
    coordinator_id = project.coordinator_conversation_id if project else None
    if coordinator_id is None or coordinator_id == member_id:
        return False
    if turn_sender != conversation_sender(coordinator_id):
        return False
    coordinator = await service.get_event_service(coordinator_id)
    if (
        coordinator is None
        or coordinator.stored.tags.get(PROJECT_TAG_KEY) != project_hex
    ):
        return False
    member = await service.get_conversation(member_id)
    message = Message(
        role="user",
        content=[
            TextContent(
                text=update_header(
                    member.title if member else None, member_id, asked=turn == "asked"
                )
            ),
            TextContent(text=update_text(text)),
        ],
    )
    try:
        await coordinator.send_message(
            message,
            run=True,
            refuse_pending_confirmation=True,
            sender=conversation_sender(member_id),
        )
    except ValueError as exc:
        if str(exc) != "waiting_for_confirmation":
            raise
    except ConversationRunLimitExceeded:
        logger.info(
            "Coordinator %s got an update but no run slot is free", coordinator_id
        )
    return True
