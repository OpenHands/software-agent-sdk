"""
Local Event router for OpenHands SDK.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from starlette.requests import Request
from starlette.responses import JSONResponse

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import (
    get_conversation_service,
    get_event_service,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConfirmationResponseRequest,
    EventSortOrder,
    MessageRunOutcome,
    ReactionRequest,
    SendMessageRequest,
    SendMessageResponse,
    Success,
)
from openhands.agent_server.openai.usage import token_usage_delta
from openhands.agent_server.project_wake import SenderError, sender_message_header
from openhands.sdk import Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import conversation_sender
from openhands.sdk.event import Event, ReactionEvent


event_read_router = APIRouter(
    prefix="/conversations/{conversation_id}/events", tags=["Events"]
)
event_write_router = APIRouter(
    prefix="/conversations/{conversation_id}/events", tags=["Events"]
)
logger = logging.getLogger(__name__)
_GATEWAY_RUN_TIMEOUT_SECONDS = 120.0


# Read methods


def normalize_datetime_to_server_timezone(dt: datetime) -> datetime:
    """
    Normalize datetime to server timezone for consistent comparison with events.

    Event timestamps are stored as naive datetimes in server local time.
    This function ensures filter datetimes are also naive in server local time
    so they can be compared correctly.

    If the datetime has timezone info, convert to server native timezone and
    strip the tzinfo to make it naive.
    If it's naive (no timezone), assume it's already in server timezone.

    Args:
        dt: Input datetime (may be timezone-aware or naive)

    Returns:
        Naive datetime in server local time
    """
    if dt.tzinfo is not None:
        # Timezone-aware: convert to server native timezone, then make naive
        return dt.astimezone(None).replace(tzinfo=None)
    else:
        # Naive datetime: assume it's already in server timezone
        return dt


@event_read_router.get(
    "/search", responses={404: {"description": "Conversation not found"}}
)
async def search_conversation_events(
    page_id: Annotated[
        str | None,
        Query(title="Optional next_page_id from the previously returned page"),
    ] = None,
    limit: Annotated[
        int,
        Query(title="The max number of results in the page", gt=0, le=100),
    ] = 100,
    kind: Annotated[
        str | None,
        Query(
            title="Optional filter by event kind/type (e.g., ActionEvent, MessageEvent)"
        ),
    ] = None,
    source: Annotated[
        str | None,
        Query(title="Optional filter by event source (e.g., agent, user, environment)"),
    ] = None,
    body: Annotated[
        str | None,
        Query(title="Optional filter by message content (case-insensitive)"),
    ] = None,
    sort_order: Annotated[
        EventSortOrder,
        Query(title="Sort order for events"),
    ] = EventSortOrder.TIMESTAMP,
    timestamp__gte: Annotated[
        datetime | None,
        Query(title="Filter: event timestamp >= this datetime"),
    ] = None,
    timestamp__lt: Annotated[
        datetime | None,
        Query(title="Filter: event timestamp < this datetime"),
    ] = None,
    event_service: EventService = Depends(get_event_service),
) -> JSONResponse:
    """Search / List local events"""

    # Normalize timezone-aware datetimes to server timezone
    normalized_gte = (
        normalize_datetime_to_server_timezone(timestamp__gte)
        if timestamp__gte
        else None
    )
    normalized_lt = (
        normalize_datetime_to_server_timezone(timestamp__lt) if timestamp__lt else None
    )

    page = await event_service.search_events(
        page_id, limit, kind, source, body, sort_order, normalized_gte, normalized_lt
    )
    if isinstance(page, dict):
        items = cast(list[Any], page.get("items", []))
        next_page_id = cast(str | None, page.get("next_page_id"))
    else:
        items = page.items
        next_page_id = page.next_page_id
    return JSONResponse(
        {
            "items": [
                dict(event)
                if isinstance(event, dict)
                else event.model_dump(mode="json", exclude_none=True)
                for event in items
            ],
            "next_page_id": next_page_id,
        }
    )


@event_read_router.get(
    "/count", responses={404: {"description": "Conversation not found"}}
)
async def count_conversation_events(
    kind: Annotated[
        str | None,
        Query(
            title="Optional filter by event kind/type (e.g., ActionEvent, MessageEvent)"
        ),
    ] = None,
    source: Annotated[
        str | None,
        Query(title="Optional filter by event source (e.g., agent, user, environment)"),
    ] = None,
    body: Annotated[
        str | None,
        Query(title="Optional filter by message content (case-insensitive)"),
    ] = None,
    timestamp__gte: Annotated[
        datetime | None,
        Query(title="Filter: event timestamp >= this datetime"),
    ] = None,
    timestamp__lt: Annotated[
        datetime | None,
        Query(title="Filter: event timestamp < this datetime"),
    ] = None,
    event_service: EventService = Depends(get_event_service),
) -> int:
    """Count local events matching the given filters"""
    # Normalize timezone-aware datetimes to server timezone
    normalized_gte = (
        normalize_datetime_to_server_timezone(timestamp__gte)
        if timestamp__gte
        else None
    )
    normalized_lt = (
        normalize_datetime_to_server_timezone(timestamp__lt) if timestamp__lt else None
    )

    count = await event_service.count_events(
        kind, source, body, normalized_gte, normalized_lt
    )

    return count


@event_read_router.get(
    "/{event_id}", responses={404: {"description": "Item not found"}}
)
async def get_conversation_event(
    event_id: str,
    event_service: EventService = Depends(get_event_service),
) -> Event:
    """Get a local event given an id"""
    event = await event_service.get_event(event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return event


@event_read_router.get("")
async def batch_get_conversation_events(
    event_ids: list[str],
    event_service: EventService = Depends(get_event_service),
) -> list[Event | None]:
    """Get a batch of local events given their ids, returning null for any
    missing item."""
    events = await event_service.batch_get_events(event_ids)
    return events


@event_write_router.post(
    "",
    response_model_exclude_none=True,
    responses={
        409: {
            "description": (
                "With refuse_pending_confirmation: the conversation is waiting "
                "for confirmation; nothing was saved"
            )
        },
        # 422 also answers when the sender conversation cannot send this
        # message; declaring it here would replace FastAPI's validation
        # error schema in the published contract.
        429: {"description": "Message saved but server run capacity is full"},
    },
)
async def send_message(
    conversation_id: UUID,
    request: SendMessageRequest,
    http_request: Request,
    event_service: EventService = Depends(get_event_service),
) -> SendMessageResponse:
    """Send a message to a conversation.

    If starting the run returns 429, the message is already saved. Retry the
    conversation's /run endpoint instead of resending the message.

    With ``run``, a conversation waiting for confirmation is normally run, which
    approves its pending actions. ``refuse_pending_confirmation`` prevents that:
    a conversation already waiting answers 409 and nothing is saved, and one
    that reaches a confirmation while the message is delivered keeps the
    message and is not run (``run: "waiting_for_confirmation"``). The refusal
    is decided under the conversation's run lock.

    With ``sender_conversation_id``, the saved message records
    ``sender: "conversation:<id hex>"`` and opens with a line telling the
    receiving agent which conversation sent it and that it approves nothing on
    the person's behalf, so UIs can show it as instructions rather than the
    person's words. A sender in a project may only message its own project's
    conversations (422 otherwise).
    """
    content = list(request.content)
    sender: str | None = None
    if request.sender_conversation_id is not None:
        try:
            header = await sender_message_header(
                get_conversation_service(http_request),
                request.sender_conversation_id,
                conversation_id,
                event_service.stored.tags,
            )
        except SenderError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)
            )
        content.insert(0, TextContent(text=header))
        sender = conversation_sender(request.sender_conversation_id)
    message = Message(role=request.role, content=content)

    guarded = request.run and request.refuse_pending_confirmation
    previous_status: ConversationExecutionStatus | None = None
    if guarded:
        # An unlocked read: it only spares the person an unrelated message in
        # a conversation already waiting. The guarded run is the safeguard.
        previous_status = (await event_service.get_state()).execution_status
        if previous_status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation is waiting for confirmation",
            )
    outcome: MessageRunOutcome | None
    try:
        outcome = await event_service.send_message(
            message,
            request.run,
            refuse_pending_confirmation=guarded,
            sender=sender,
        )
    except ValueError as e:
        if not guarded or str(e) != "waiting_for_confirmation":
            raise
        outcome = "waiting_for_confirmation"
    if request.run and outcome is None:
        outcome = "not_run"
    return SendMessageResponse(run=outcome, previous_status=previous_status)


async def _gateway_context(
    conversation_id: UUID,
    http_request: Request,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> tuple[EventService, float]:
    deadline = time.monotonic() + _GATEWAY_RUN_TIMEOUT_SECONDS
    event_service = await get_event_service(
        conversation_id, http_request, conversation_service
    )
    return event_service, deadline


@event_write_router.post("/_gateway/run", include_in_schema=False)
async def _gateway_run_endpoint(
    request: SendMessageRequest,
    context: tuple[EventService, float] = Depends(_gateway_context),
) -> dict[str, Any]:
    event_service, deadline = context
    return await run_gateway_message(request, event_service, deadline=deadline)


async def run_gateway_message(
    request: SendMessageRequest,
    event_service: EventService,
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Run one gateway turn at a time for this conversation."""
    if deadline is None:
        deadline = time.monotonic() + _GATEWAY_RUN_TIMEOUT_SECONDS
    try:
        await asyncio.wait_for(
            event_service._gateway_run_lock.acquire(),
            timeout=deadline - time.monotonic(),
        )
    except TimeoutError as exc:
        raise HTTPException(504, "Agent run timed out") from exc
    try:
        return await _run_gateway_message(request, event_service, deadline)
    finally:
        event_service._gateway_run_lock.release()


async def _run_gateway_message(
    request: SendMessageRequest, event_service: EventService, deadline: float
) -> dict[str, Any]:
    """Save the message only after run capacity is available."""
    if event_service._run_task is not None:
        try:
            await event_service.wait_for_run_completion(
                timeout=max(0, deadline - time.monotonic())
            )
        except TimeoutError as exc:
            raise HTTPException(504, "Agent run timed out") from exc
    state_before = await event_service.get_state()
    min_event_count = len(state_before.events) + 1
    usage_before = state_before.stats.get_combined_metrics().accumulated_token_usage
    run_slot = await event_service.reserve_run_slot()
    try:
        if time.monotonic() >= deadline:
            raise HTTPException(504, "Agent run timed out")
        await event_service.send_message(
            request.create_message(), run=True, run_slot=run_slot
        )
    finally:
        if run_slot is not None:
            run_slot.release()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPException(504, "Agent run timed out")
        try:
            run_status = await event_service.wait_for_run_completion(timeout=remaining)
        except TimeoutError as exc:
            raise HTTPException(504, "Agent run timed out") from exc
        state = await event_service.get_state()
        if len(state.events) > min_event_count and (
            run_status.is_terminal()
            or run_status
            in (
                ConversationExecutionStatus.PAUSED,
                ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
            )
        ):
            break
        await asyncio.sleep(0.1)
    usage = token_usage_delta(
        usage_before, state.stats.get_combined_metrics().accumulated_token_usage
    )
    return {
        "status": run_status.value,
        "final_response": await event_service.get_agent_final_response(),
        "usage": usage.model_dump(mode="json") if usage is not None else None,
    }


@event_write_router.post(
    "/respond_to_confirmation",
    responses={
        404: {"description": "Item not found"},
        429: {"description": "Server conversation run capacity is full"},
    },
)
async def respond_to_confirmation(
    request: ConfirmationResponseRequest,
    event_service: EventService = Depends(get_event_service),
) -> Success:
    """Accept or reject a pending action in confirmation mode."""
    await event_service.respond_to_confirmation(request)
    return Success()


reaction_router = APIRouter(
    prefix="/conversations/{conversation_id}/reactions", tags=["Events"]
)


@reaction_router.post(
    "",
    responses={
        400: {"description": "There is no such message to react to"},
        404: {"description": "Item not found"},
    },
)
async def react_to_message(
    request: ReactionRequest,
    event_service: EventService = Depends(get_event_service),
) -> ReactionEvent:
    """Add or remove the person's emoji reaction on a message.

    The reaction is saved as a `ReactionEvent` and streamed like other events;
    it does not run the agent. Without `target_event_id` it goes on the
    agent's latest message.
    """
    try:
        return await event_service.react(
            request.emoji, request.target_event_id, removed=request.removed
        )
    except ValueError as exc:
        if str(exc) == "inactive_service":
            raise
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


event_router = APIRouter()
event_router.include_router(event_read_router)
event_router.include_router(event_write_router)
event_router.include_router(reaction_router)
