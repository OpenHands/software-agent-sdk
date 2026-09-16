"""Bash router for OpenHands SDK."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.dependencies import get_bash_event_service
from openhands.agent_server.models import (
    BashCommand,
    BashEventBase,
    BashEventPage,
    BashEventSortOrder,
    BashOutput,
    ExecuteBashRequest,
)
from openhands.agent_server.persistence import get_secrets_store
from openhands.agent_server.profile_secrets import command_secret_registry
from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.sdk.conversation.secret_registry import SecretRegistry


bash_router = APIRouter(prefix="/bash", tags=["Bash"])
logger = logging.getLogger(__name__)


# bash event routes
@bash_router.get("/bash_events/search")
async def search_bash_events(
    kind__eq: Literal["BashCommand", "BashOutput"] | None = None,
    command_id__eq: UUID | None = None,
    timestamp__gte: datetime | None = None,
    timestamp__lt: datetime | None = None,
    order__gt: Annotated[
        int | None,
        Query(
            title="Filter to events with order greater than this value",
            description="Only returns BashOutput events with order > this value. "
            "Useful for polling to fetch only new events since the last poll.",
        ),
    ] = None,
    sort_order: BashEventSortOrder = BashEventSortOrder.TIMESTAMP,
    page_id: Annotated[
        str | None,
        Query(title="Optional next_page_id from the previously returned page"),
    ] = None,
    limit: Annotated[
        int,
        Query(title="The max number of results in the page", gt=0, le=100),
    ] = 100,
    bash_event_service: BashEventService = Depends(get_bash_event_service),
) -> BashEventPage:
    """Search / List bash event events"""

    return await bash_event_service.search_bash_events(
        kind__eq=kind__eq,
        command_id__eq=command_id__eq,
        timestamp__gte=timestamp__gte,
        timestamp__lt=timestamp__lt,
        order__gt=order__gt,
        sort_order=sort_order,
        page_id=page_id,
        limit=limit,
    )


@bash_router.get(
    "/bash_events/{event_id}", responses={404: {"description": "Item not found"}}
)
async def get_bash_event(
    event_id: str,
    bash_event_service: BashEventService = Depends(get_bash_event_service),
) -> BashEventBase:
    """Get a bash event event given an id"""
    event = await bash_event_service.get_bash_event(event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return event


@bash_router.get("/bash_events/")
async def batch_get_bash_events(
    event_ids: list[str],
    bash_event_service: BashEventService = Depends(get_bash_event_service),
) -> list[BashEventBase | None]:
    """Get a batch of bash event events given their ids, returning null for any
    missing item."""
    events = await bash_event_service.batch_get_bash_events(event_ids)
    return events


@bash_router.post("/start_bash_command")
async def start_bash_command(
    body: ExecuteBashRequest,
    request: Request,
    bash_event_service: BashEventService = Depends(get_bash_event_service),
) -> BashCommand:
    """Execute a bash command in the background"""
    update_last_execution_time()
    _validate_cwd(body, bash_event_service)
    registry = await _command_registry(body, request, bash_event_service)
    command, _ = await bash_event_service.start_bash_command(body, registry)
    return command


@bash_router.post("/execute_bash_command")
async def execute_bash_command(
    body: ExecuteBashRequest,
    request: Request,
    bash_event_service: BashEventService = Depends(get_bash_event_service),
) -> BashOutput:
    """Execute a bash command and wait for a result"""
    update_last_execution_time()
    _validate_cwd(body, bash_event_service)
    registry = await _command_registry(body, request, bash_event_service)
    command, task = await bash_event_service.start_bash_command(body, registry)
    await task
    page = await bash_event_service.search_bash_events(command_id__eq=command.id)
    result = cast(BashOutput, page.items[-1])
    return result


@bash_router.delete("/bash_events")
async def clear_all_bash_events(
    bash_event_service: BashEventService = Depends(get_bash_event_service),
) -> dict[str, int]:
    """Clear all bash events from storage"""
    count = await bash_event_service.clear_all_events()
    return {"cleared_count": count}


def _validate_cwd(request: ExecuteBashRequest, service: BashEventService) -> None:
    if service.default_cwd is not None and request.cwd is not None:
        if (
            not Path(request.cwd)
            .resolve()
            .is_relative_to(Path(service.default_cwd).resolve())
        ):
            raise HTTPException(422, "cwd must be inside the conversation workspace")


async def _command_registry(
    body: ExecuteBashRequest,
    request: Request,
    service: BashEventService,
) -> SecretRegistry | None:
    profile_id = body.agent_profile_id
    if profile_id is None:
        return None
    if service.secret_registry is not None:
        raise HTTPException(
            422, "A conversation-scoped command cannot select another profile"
        )
    try:
        return await asyncio.to_thread(
            command_secret_registry,
            profile_id,
            get_secrets_store(request.app.state.config),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
