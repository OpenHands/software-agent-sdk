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
    person_message_header,
    person_turn_header,
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
    from openhands.agent_server.event_service import EventService


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


async def _coordinator(
    service: "ConversationService", member_id: UUID, project_hex: str
) -> tuple[UUID, "EventService"] | None:
    """The coordinator a member's news goes to, when there is one to tell."""
    persisted = await asyncio.to_thread(get_projects_store().load)
    if persisted is None:
        return None
    project = next((p for p in persisted.projects if p.id.hex == project_hex), None)
    coordinator_id = project.coordinator_conversation_id if project else None
    if coordinator_id is None or coordinator_id == member_id:
        return None
    coordinator = await service.get_event_service(coordinator_id)
    if (
        coordinator is None
        or coordinator.stored.tags.get(PROJECT_TAG_KEY) != project_hex
    ):
        return None
    return coordinator_id, coordinator


async def _deliver(
    coordinator_id: UUID,
    coordinator: "EventService",
    member_id: UUID,
    content: list[TextContent],
    *,
    run: bool,
) -> None:
    try:
        await coordinator.send_message(
            Message(role="user", content=list(content)),
            run=run,
            refuse_pending_confirmation=run,
            sender=conversation_sender(member_id),
        )
    except ValueError as exc:
        if str(exc) != "waiting_for_confirmation":
            raise
    except ConversationRunLimitExceeded:
        logger.info(
            "Coordinator %s got an update but no run slot is free", coordinator_id
        )


async def notify_coordinator(
    service: "ConversationService",
    member_id: UUID,
    project_hex: str,
    turn: TurnOutcome,
    text: str | None,
    turn_sender: str | None,
) -> bool:
    """Deliver a member's final message to its project's coordinator.

    A turn the coordinator started (its message, or the task message of a
    child it started) wakes the coordinator: it waits for that reply. A turn
    the person started in the member is delivered without a run, for the
    coordinator to know on its next turn; the person already saw the reply.
    A turn another conversation started is not delivered, so an update the
    coordinator answered never starts another round.

    Returns whether a message was saved. Nothing is sent when the project or
    its coordinator is gone, when the coordinator is the member itself, or
    when the coordinator left the project.
    """
    by_person = turn_sender is None
    if not by_person and not str(turn_sender).startswith(CONVERSATION_SENDER_PREFIX):
        return False
    found = await _coordinator(service, member_id, project_hex)
    if found is None:
        return False
    coordinator_id, coordinator = found
    if not by_person and turn_sender != conversation_sender(coordinator_id):
        return False
    member = await service.get_conversation(member_id)
    title = member.title if member else None
    asked = turn == "asked"
    header = (
        person_turn_header(title, member_id, asked=asked)
        if by_person
        else update_header(title, member_id, asked=asked)
    )
    await _deliver(
        coordinator_id,
        coordinator,
        member_id,
        [TextContent(text=header), TextContent(text=update_text(text))],
        run=not by_person,
    )
    return True


async def relay_person_message(
    service: "ConversationService",
    member_id: UUID,
    project_hex: str,
    text: str,
) -> bool:
    """Copy a message the person wrote in a member to the project's coordinator.

    The copy is saved without running the coordinator: it is for the
    coordinator to know what the person asked where, on its next turn, and
    waking it for every message would only tempt it to forward the message
    back. Returns whether a copy was saved.
    """
    if not text.strip():
        return False
    found = await _coordinator(service, member_id, project_hex)
    if found is None:
        return False
    coordinator_id, coordinator = found
    member = await service.get_conversation(member_id)
    await _deliver(
        coordinator_id,
        coordinator,
        member_id,
        [
            TextContent(
                text=person_message_header(member.title if member else None, member_id)
            ),
            TextContent(text=update_text(text)),
        ],
        run=False,
    )
    return True
