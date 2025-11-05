"""Bash router for OpenHands SDK."""

import logging
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    status,
)

from openhands.agent_server.bash_service import get_default_bash_event_service
from openhands.agent_server.models import (
    BashCommand,
    BashEventBase,
    BashEventPage,
    BashEventSortOrder,
    BashOutput,
    ExecuteBashRequest,
)


bash_router = APIRouter(prefix="/bash", tags=["Bash"])
bash_event_service = get_default_bash_event_service()
logger = logging.getLogger(__name__)


# bash event routes
@bash_router.get("/bash_events/search")
async def search_bash_events(
    kind__eq: Literal["BashCommand", "BashOutput"] | None = None,
    command_id__eq: UUID | None = None,
    timestamp__gte: datetime | None = None,
    timestamp__lt: datetime | None = None,
    sort_order: BashEventSortOrder = BashEventSortOrder.TIMESTAMP,
    page_id: Annotated[
        str | None,
        Query(title="Optional next_page_id from the previously returned page"),
    ] = None,
    limit: Annotated[
        int,
        Query(title="The max number of results in the page", gt=0, lte=100),
    ] = 100,
) -> BashEventPage:
    """Search / List bash event events"""
    assert limit > 0
    assert limit <= 100

    return await bash_event_service.search_bash_events(
        kind__eq=kind__eq,
        command_id__eq=command_id__eq,
        timestamp__gte=timestamp__gte,
        timestamp__lt=timestamp__lt,
        sort_order=sort_order,
        page_id=page_id,
        limit=limit,
    )


@bash_router.get(
    "/bash_events/{event_id}", responses={404: {"description": "Item not found"}}
)
async def get_bash_event(event_id: str) -> BashEventBase:
    """Get a bash event event given an id"""
    event = await bash_event_service.get_bash_event(event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return event


@bash_router.get("/bash_events/")
async def batch_get_bash_events(
    event_ids: list[str],
) -> list[BashEventBase | None]:
    """Get a batch of bash event events given their ids, returning null for any
    missing item."""
    events = await bash_event_service.batch_get_bash_events(event_ids)
    return events


@bash_router.post("/start_bash_command")
async def start_bash_command(request: ExecuteBashRequest) -> BashCommand:
    """Execute a bash command in the background"""
    command, _ = await bash_event_service.start_bash_command(request)
    return command


@bash_router.post("/execute_bash_command")
async def execute_bash_command(request: ExecuteBashRequest) -> BashOutput:
    """Execute a bash command and wait for a result"""
    command, task = await bash_event_service.start_bash_command(request)
    await task
    page = await bash_event_service.search_bash_events(command_id__eq=command.id)
    result = cast(BashOutput, page.items[-1])
    return result


@bash_router.delete("/bash_events")
async def clear_all_bash_events() -> dict[str, int]:
    """Clear all bash events from storage"""
    count = await bash_event_service.clear_all_events()
    return {"cleared_count": count}
