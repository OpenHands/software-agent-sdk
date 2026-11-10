"""Messages one conversation sends another, and the coordinator's wake-up.

:func:`sender_message_header` checks a ``sender_conversation_id`` given to
``POST /api/conversations/{id}/events`` and writes the line the message opens
with. :func:`notify_coordinator` wakes a project's coordinator when one of its
conversations finishes a run the coordinator asked for.

The coordinator gets the member's final message, not its events: one user
message that records the member as ``sender`` and opens with
:func:`~openhands.agent_server.project_prompts.update_header`. A member the
coordinator started that stops on the person instead (confirmation, error,
stuck) sends :func:`~openhands.agent_server.project_prompts.stop_header`, and a
pause is saved for awareness (:func:`notify_coordinator_of_stop`). Updates are
delivered like a message sent with ``refuse_pending_confirmation``, so they
never run a coordinator that waits on the person (the message is kept and seen
on its next run). The coordinator reads more with ``read_project_conversation``.

:func:`reconcile_coordinator_updates` catches up after a restart: a member
whose coordinator-started turn ended while no update reached the coordinator
(the server stopped first, or every delivery attempt failed) is delivered
then, from its event log.
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from openhands.agent_server.event_service import ConversationRunLimitExceeded
from openhands.agent_server.experimental_features import feature_enabled_now
from openhands.agent_server.models import EventSortOrder
from openhands.agent_server.persistence import get_projects_store
from openhands.agent_server.project_board import (
    TurnOutcome,
    describe_action,
    starts_turn,
)
from openhands.agent_server.project_prompts import (
    SenderRole,
    paused_header,
    person_message_header,
    person_turn_header,
    sender_header,
    stop_header,
    update_header,
    update_text,
)
from openhands.sdk import Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.title_utils import extract_message_text
from openhands.sdk.conversation.types import (
    CONVERSATION_SENDER_PREFIX,
    HELD_FOR_PERSON_STATUSES,
    PROJECT_TAG_KEY,
    TURN_TAG_KEY,
    conversation_sender,
)
from openhands.sdk.event import ActionEvent, Event, MessageEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.builtins.finish import FinishAction


if TYPE_CHECKING:
    from openhands.agent_server.conversation_service import ConversationService
    from openhands.agent_server.event_service import EventService


logger = get_logger(__name__)

_DELIVERY_ATTEMPTS = 3
_DELIVERY_RETRY_SECONDS = (1.0, 5.0)
# Runs waiting for a free slot, kept so they are not garbage collected.
_waiting_runs: set[asyncio.Task] = set()


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
    """The coordinator a member's news goes to, when there is one to tell.

    None while Projects is off: members' news and the person's copies wait.
    """
    if not await feature_enabled_now("projects"):
        return None
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
    """Save a message in the coordinator and, with ``run``, run it.

    The run is guarded like any message not from the person: a coordinator
    that waits on the person keeps the message for its next run. When every
    run slot is taken, a background task waits for one instead of leaving the
    update unread. A failed save is retried before the update is given up.
    """
    message = Message(role="user", content=list(content))
    for attempt in range(_DELIVERY_ATTEMPTS):
        last = attempt == _DELIVERY_ATTEMPTS - 1
        try:
            await coordinator.send_message(
                message,
                run=run,
                refuse_pending_confirmation=run,
                sender=conversation_sender(member_id),
                keep_goal_loop=True,
            )
            return
        except ConversationRunLimitExceeded:
            _run_when_free(coordinator_id, coordinator)
            return
        except ValueError as exc:
            if str(exc) in HELD_FOR_PERSON_STATUSES:
                return
            if str(exc) == "inactive_service" or last:
                raise
        except Exception:
            if last:
                raise
        delay = _DELIVERY_RETRY_SECONDS[min(attempt, len(_DELIVERY_RETRY_SECONDS) - 1)]
        logger.info(
            "Delivering to coordinator %s failed; retrying in %.0fs",
            coordinator_id,
            delay,
        )
        await asyncio.sleep(delay)


def _run_when_free(coordinator_id: UUID, coordinator: "EventService") -> None:
    """Run the coordinator for a saved update once a run slot frees."""

    async def wait_and_run() -> None:
        try:
            await coordinator.run_saved_message_when_free()
        except ValueError as exc:
            if str(exc) not in HELD_FOR_PERSON_STATUSES:
                logger.warning(
                    "Coordinator %s did not run for an update: %s",
                    coordinator_id,
                    exc,
                )
        except Exception:
            logger.warning(
                "Coordinator %s did not run for an update",
                coordinator_id,
                exc_info=True,
            )

    logger.info("Coordinator %s got an update; waiting for a run slot", coordinator_id)
    task = asyncio.create_task(wait_and_run())
    _waiting_runs.add(task)
    task.add_done_callback(_waiting_runs.discard)


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


async def notify_coordinator_of_stop(
    service: "ConversationService",
    member_id: UUID,
    project_hex: str,
    status: str,
    text: str | None,
    turn_sender: str | None,
) -> bool:
    """Tell the coordinator that a member it started stopped on the person.

    Only for a turn the coordinator started; the person sees their own turns
    stop. Waiting for confirmation, an error or stuck wakes the coordinator so
    it can tell the person what the member waits on. A pause is the person's
    own act, so it is saved for the coordinator's awareness without a run.
    Returns whether a message was saved.
    """
    if status not in HELD_FOR_PERSON_STATUSES or turn_sender is None:
        return False
    found = await _coordinator(service, member_id, project_hex)
    if found is None:
        return False
    coordinator_id, coordinator = found
    if turn_sender != conversation_sender(coordinator_id):
        return False
    member = await service.get_conversation(member_id)
    title = member.title if member else None
    paused = status == "paused"
    header = (
        paused_header(title, member_id)
        if paused
        else stop_header(title, member_id, status=status)
    )
    await _deliver(
        coordinator_id,
        coordinator,
        member_id,
        [TextContent(text=header), TextContent(text=update_text(text))],
        run=not paused,
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


_TURN_LOOKBACK = 50
_ENDED = HELD_FOR_PERSON_STATUSES | {ConversationExecutionStatus.FINISHED.value}


async def latest_turn_start(event_service: "EventService") -> MessageEvent | None:
    """The latest user message that started a turn, from the event log."""
    page = await event_service.search_events(
        kind="MessageEvent",
        source="user",
        sort_order=EventSortOrder.TIMESTAMP_DESC,
        limit=_TURN_LOOKBACK,
    )
    start = next((e for e in page.items if starts_turn(e)), None)
    return start if isinstance(start, MessageEvent) else None


def _since(timestamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def _final_text(event: Event) -> str | None:
    if isinstance(event, MessageEvent):
        return extract_message_text(event)
    if isinstance(event, ActionEvent):
        if isinstance(event.action, FinishAction):
            return event.action.message
        return describe_action(event)
    return None


async def _reconcile_member(
    service: "ConversationService",
    project_hex: str,
    coordinator_id: UUID,
    coordinator: "EventService",
    member_id: UUID,
    status: str,
    tags: dict[str, str],
) -> bool:
    member = await service.get_event_service(member_id)
    if member is None:
        return False
    start = await latest_turn_start(member)
    if start is None or start.sender != conversation_sender(coordinator_id):
        return False
    since = _since(start.timestamp)
    output = await member.search_events(
        source="agent",
        sort_order=EventSortOrder.TIMESTAMP_DESC,
        timestamp__gte=since,
        limit=_TURN_LOOKBACK,
    )
    last = next(
        (e for e in output.items if isinstance(e, (MessageEvent, ActionEvent))),
        None,
    )
    if last is None:
        return False
    # Delivered when the coordinator holds a message from the member saved
    # after the member's last output of this turn.
    sent = await coordinator.search_events(
        kind="MessageEvent",
        source="user",
        sort_order=EventSortOrder.TIMESTAMP_DESC,
        timestamp__gte=_since(last.timestamp),
        limit=_TURN_LOOKBACK,
    )
    if any(
        isinstance(e, MessageEvent) and e.sender == conversation_sender(member_id)
        for e in sent.items
    ):
        return False
    text = next(
        (t for t in (_final_text(e) for e in output.items) if t),
        None,
    )
    logger.info(
        "Delivering a missed update from %s to coordinator %s",
        member_id,
        coordinator_id,
    )
    if status == ConversationExecutionStatus.FINISHED.value:
        turn: TurnOutcome = "asked" if tags.get(TURN_TAG_KEY) == "asked" else "answered"
        return await notify_coordinator(
            service, member_id, project_hex, turn, text, start.sender
        )
    if status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION.value:
        text = _final_text(last) or text
    return await notify_coordinator_of_stop(
        service, member_id, project_hex, status, text, start.sender
    )


async def reconcile_coordinator_updates(service: "ConversationService") -> int:
    """Deliver updates a coordinator should have had but never got.

    For each project member whose latest turn the coordinator started and
    that has ended (finished, or stopped on the person) with no message from
    it in the coordinator since its last output, the update is delivered now,
    as it would have been when the turn ended. Turns the person started are
    not replayed: the person saw those. Returns how many were delivered.
    """
    if not await feature_enabled_now("projects"):
        return 0
    persisted = await asyncio.to_thread(get_projects_store().load)
    if persisted is None:
        return 0
    delivered = 0
    for project in persisted.projects:
        coordinator_id = project.coordinator_conversation_id
        if coordinator_id is None:
            continue
        project_hex = project.id.hex
        members = await service.list_tagged_conversations(PROJECT_TAG_KEY, project_hex)
        for stored, execution_status in members:
            status = str(getattr(execution_status, "value", execution_status))
            if stored.id == coordinator_id or status not in _ENDED:
                continue
            target = await _coordinator(service, stored.id, project_hex)
            if target is None:
                break
            try:
                if await _reconcile_member(
                    service,
                    project_hex,
                    target[0],
                    target[1],
                    stored.id,
                    status,
                    dict(stored.tags),
                ):
                    delivered += 1
            except Exception:
                logger.warning(
                    "Catching up on %s for coordinator %s failed",
                    stored.id,
                    coordinator_id,
                    exc_info=True,
                )
    return delivered
