"""
WebSocket endpoints for OpenHands SDK.

These endpoints are separate from the main API routes to handle WebSocket-specific
authentication using query parameters instead of headers, since browsers cannot
send custom HTTP headers directly with WebSocket connections.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from openhands.agent_server.bash_service import get_default_bash_event_service
from openhands.agent_server.config import get_default_config
from openhands.agent_server.conversation_service import (
    get_default_conversation_service,
)
from openhands.agent_server.event_router import normalize_datetime_to_server_timezone
from openhands.agent_server.models import BashEventBase, ExecuteBashRequest
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import Event, Message
from openhands.sdk.utils.paging import page_iterator


sockets_router = APIRouter(prefix="/sockets", tags=["WebSockets"])
conversation_service = get_default_conversation_service()
bash_event_service = get_default_bash_event_service()
logger = logging.getLogger(__name__)


@sockets_router.websocket("/events/{conversation_id}")
async def events_socket(
    conversation_id: UUID,
    websocket: WebSocket,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    resend_all: Annotated[bool, Query()] = False,
    after_timestamp: Annotated[
        datetime | None,
        Query(
            description=(
                "Filter events to timestamps >= this value when resend_all=True. "
                "Accepts ISO 8601 format. Timezone-aware datetimes are converted "
                "to server local time; naive datetimes assumed in server timezone."
            )
        ),
    ] = None,
):
    """WebSocket endpoint for conversation events.

    Args:
        conversation_id: The conversation ID to subscribe to.
        websocket: The WebSocket connection.
        session_api_key: Optional API key for authentication.
        resend_all: If True, resend all existing events when connecting.
        after_timestamp: If provided with resend_all=True, only resend events
            with timestamps >= this value. Timestamps are interpreted in server
            local time. Timezone-aware datetimes are converted to server timezone.
            Enables efficient bi-directional loading where REST fetches historical
            events and WebSocket handles events after a specific point in time.
    """
    # Perform authentication check before accepting the WebSocket connection
    config = get_default_config()
    if config.session_api_keys and session_api_key not in config.session_api_keys:
        # Close the WebSocket connection with an authentication error code
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await websocket.accept()
    logger.info(f"Event Websocket Connected: {conversation_id}")
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        logger.warning(f"Converation not found: {conversation_id}")
        await websocket.close(code=4004, reason="Conversation not found")
        return

    subscriber_id = await event_service.subscribe_to_events(
        _WebSocketSubscriber(websocket)
    )

    # Normalize timezone-aware datetimes to server timezone
    normalized_after_timestamp = (
        normalize_datetime_to_server_timezone(after_timestamp)
        if after_timestamp
        else None
    )

    # Warn if after_timestamp is provided without resend_all
    if after_timestamp and not resend_all:
        logger.warning(
            f"after_timestamp provided without resend_all=True, "
            f"will be ignored: {conversation_id}"
        )

    try:
        # Resend existing events if requested
        if resend_all:
            if normalized_after_timestamp:
                logger.info(
                    f"Resending events after {normalized_after_timestamp}: "
                    f"{conversation_id}"
                )
            else:
                logger.info(f"Resending all events: {conversation_id}")
            async for event in page_iterator(
                event_service.search_events, timestamp__gte=normalized_after_timestamp
            ):
                await _send_event(event, websocket)

        # Listen for messages over the socket
        while True:
            try:
                data = await websocket.receive_json()
                logger.info(f"Received message: {conversation_id}")
                message = Message.model_validate(data)
                await event_service.send_message(message, True)
            except WebSocketDisconnect:
                logger.info(f"Event websocket disconnected: {conversation_id}")
                # Exit the loop when websocket disconnects
                return
            except Exception as e:
                logger.exception("error_in_subscription", stack_info=True)
                # For critical errors that indicate the websocket is broken, exit
                if isinstance(e, (RuntimeError, ConnectionError)):
                    raise
                # For other exceptions, continue the loop
    finally:
        await event_service.unsubscribe_from_events(subscriber_id)


@sockets_router.websocket("/bash-events")
async def bash_events_socket(
    websocket: WebSocket,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    resend_all: Annotated[bool, Query()] = False,
):
    """WebSocket endpoint for bash events."""
    # Perform authentication check before accepting the WebSocket connection
    config = get_default_config()
    if config.session_api_keys and session_api_key not in config.session_api_keys:
        # Close the WebSocket connection with an authentication error code
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await websocket.accept()
    logger.info("Bash Websocket Connected")
    subscriber_id = await bash_event_service.subscribe_to_events(
        _BashWebSocketSubscriber(websocket)
    )
    try:
        # Resend all existing events if requested
        if resend_all:
            logger.info("Resending bash events")
            async for event in page_iterator(bash_event_service.search_bash_events):
                await _send_bash_event(event, websocket)

        while True:
            try:
                # Keep the connection alive and handle any incoming messages
                data = await websocket.receive_json()
                logger.info("Received bash request")
                request = ExecuteBashRequest.model_validate(data)
                await bash_event_service.start_bash_command(request)
            except WebSocketDisconnect:
                # Exit the loop when websocket disconnects
                logger.info("Bash websocket disconnected")
                return
            except Exception as e:
                logger.exception("error_in_bash_event_subscription", stack_info=True)
                # For critical errors that indicate the websocket is broken, exit
                if isinstance(e, (RuntimeError, ConnectionError)):
                    raise
                # For other exceptions, continue the loop
    finally:
        await bash_event_service.unsubscribe_from_events(subscriber_id)


async def _send_event(event: Event, websocket: WebSocket):
    try:
        dumped = event.model_dump(mode="json")
        await websocket.send_json(dumped)
    except Exception:
        logger.exception("error_sending_event: %r", event, stack_info=True)


@dataclass
class _WebSocketSubscriber(Subscriber):
    """WebSocket subscriber for conversation events."""

    websocket: WebSocket

    async def __call__(self, event: Event):
        await _send_event(event, self.websocket)


async def _send_bash_event(event: BashEventBase, websocket: WebSocket):
    try:
        dumped = event.model_dump(mode="json")
        await websocket.send_json(dumped)
    except Exception:
        logger.exception("error_sending_bash_event: %r", event, stack_info=True)


@dataclass
class _BashWebSocketSubscriber(Subscriber[BashEventBase]):
    """WebSocket subscriber for bash events."""

    websocket: WebSocket

    async def __call__(self, event: BashEventBase):
        await _send_bash_event(event, self.websocket)
