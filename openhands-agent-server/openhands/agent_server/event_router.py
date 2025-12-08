"""
Local Event router for OpenHands SDK.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)

from openhands.agent_server.dependencies import get_event_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConfirmationResponseRequest,
    EventPage,
    EventSortOrder,
    SendMessageRequest,
    Success,
)
from openhands.sdk import Message
from openhands.sdk.event import Event


event_router = APIRouter(
    prefix="/conversations/{conversation_id}/events", tags=["Events"]
)
logger = logging.getLogger(__name__)


# Read methods


def normalize_datetime_to_server_timezone(dt: datetime) -> datetime:
    """
    Normalize datetime to server timezone for consistent comparison.

    If the datetime has timezone info, convert to server native timezone.
    If it's naive (no timezone), assume it's already in server timezone.

    Args:
        dt: Input datetime (may be timezone-aware or naive)

    Returns:
        Datetime in server native timezone (timezone-aware)
    """
    if dt.tzinfo is not None:
        # Timezone-aware: convert to server native timezone
        return dt.astimezone(None)
    else:
        # Naive datetime: assume it's already in server timezone
        return dt


@event_router.get("/search", responses={404: {"description": "Conversation not found"}})
async def search_conversation_events(
    page_id: Annotated[
        str | None,
        Query(title="Optional next_page_id from the previously returned page"),
    ] = None,
    limit: Annotated[
        int,
        Query(title="The max number of results in the page", gt=0, lte=100),
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
) -> EventPage:
    """Search / List local events"""
    assert limit > 0
    assert limit <= 100

    # Normalize timezone-aware datetimes to server timezone
    normalized_gte = (
        normalize_datetime_to_server_timezone(timestamp__gte)
        if timestamp__gte
        else None
    )
    normalized_lt = (
        normalize_datetime_to_server_timezone(timestamp__lt) if timestamp__lt else None
    )

    return await event_service.search_events(
        page_id, limit, kind, source, body, sort_order, normalized_gte, normalized_lt
    )


@event_router.get("/count", responses={404: {"description": "Conversation not found"}})
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


@event_router.get("/{event_id}", responses={404: {"description": "Item not found"}})
async def get_conversation_event(
    event_id: str,
    event_service: EventService = Depends(get_event_service),
) -> Event:
    """Get a local event given an id"""
    event = await event_service.get_event(event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return event


@event_router.get("")
async def batch_get_conversation_events(
    event_ids: list[str],
    event_service: EventService = Depends(get_event_service),
) -> list[Event | None]:
    """Get a batch of local events given their ids, returning null for any
    missing item."""
    events = await event_service.batch_get_events(event_ids)
    return events


@event_router.post("")
async def send_message(
    request: SendMessageRequest,
    event_service: EventService = Depends(get_event_service),
) -> Success:
    """Send a message to a conversation"""
    message = Message(role=request.role, content=request.content)
    await event_service.send_message(message, request.run)
    return Success()


@event_router.post(
    "/respond_to_confirmation", responses={404: {"description": "Item not found"}}
)
async def respond_to_confirmation(
    request: ConfirmationResponseRequest,
    event_service: EventService = Depends(get_event_service),
) -> Success:
    """Accept or reject a pending action in confirmation mode."""
    await event_service.respond_to_confirmation(request)
    return Success()
