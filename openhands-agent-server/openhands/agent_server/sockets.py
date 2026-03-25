"""
WebSocket endpoints for OpenHands SDK.

These endpoints are separate from the main API routes to handle WebSocket-specific
authentication. Browsers cannot send custom HTTP headers directly with WebSocket
connections, so we support the `session_api_key` query param. For non-browser
clients (e.g. Python/Node), we also support authenticating via headers.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from openhands.agent_server.bash_service import get_default_bash_event_service
from openhands.agent_server.config import Config, get_default_config
from openhands.agent_server.conversation_service import (
    get_default_conversation_service,
)
from openhands.agent_server.event_router import normalize_datetime_to_server_timezone
from openhands.agent_server.models import BashEventBase, ExecuteBashRequest
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import Event, Message
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.mcp.exceptions import MCPError
from openhands.sdk.utils.paging import page_iterator


sockets_router = APIRouter(prefix="/sockets", tags=["WebSockets"])
conversation_service = get_default_conversation_service()
bash_event_service = get_default_bash_event_service()
logger = logging.getLogger(__name__)


def _get_config(websocket: WebSocket) -> Config:
    """Return the Config associated with this FastAPI app instance.

    This ensures WebSocket auth follows the same configuration as the REST API
    when the agent server is used as a library (e.g., tests or when mounted into
    another FastAPI app), rather than always reading environment defaults.
    """
    config = getattr(websocket.app.state, "config", None)
    if isinstance(config, Config):
        return config
    return get_default_config()


def _resolve_websocket_session_api_key(
    websocket: WebSocket,
    session_api_key: str | None,
) -> str | None:
    """Resolve the session API key from multiple sources.

    Precedence order (highest to lowest):
    1. Query parameter (session_api_key) - for browser compatibility
    2. X-Session-API-Key header - for non-browser clients

    Returns None if no key is provided in any source.
    """
    if session_api_key is not None:
        return session_api_key

    header_key = websocket.headers.get("x-session-api-key")
    if header_key is not None:
        return header_key

    return None


async def _accept_authenticated_websocket(
    websocket: WebSocket,
    session_api_key: str | None,
) -> bool:
    """Authenticate and accept the socket, or close with an auth error."""
    config = _get_config(websocket)
    resolved_key = _resolve_websocket_session_api_key(websocket, session_api_key)
    if config.session_api_keys and resolved_key not in config.session_api_keys:
        logger.warning("WebSocket authentication failed: invalid or missing API key")
        await websocket.close(code=4001, reason="Authentication failed")
        return False
    await websocket.accept()
    return True


@sockets_router.websocket("/events/{conversation_id}")
async def events_socket(
    conversation_id: UUID,
    websocket: WebSocket,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    resend_mode: Annotated[
        Literal["all", "since"] | None,
        Query(
            description=(
                "Mode for resending historical events on connect. "
                "'all' sends all events, 'since' sends events after 'after_timestamp'."
            )
        ),
    ] = None,
    after_timestamp: Annotated[
        datetime | None,
        Query(
            description=(
                "Required when resend_mode='since'. Events with timestamp >= this "
                "value will be sent. Accepts ISO 8601 format. Timezone-aware "
                "datetimes are converted to server local time; naive datetimes "
                "assumed in server timezone."
            )
        ),
    ] = None,
    # Deprecated parameter - kept for backward compatibility
    resend_all: Annotated[
        bool,
        Query(
            include_in_schema=False,
            deprecated=True,
        ),
    ] = False,
):
    """WebSocket endpoint for conversation events.

    Args:
        conversation_id: The conversation ID to subscribe to.
        websocket: The WebSocket connection.
        session_api_key: Optional API key for authentication.
        resend_mode: Mode for resending historical events on connect.
            - 'all': Resend all existing events
            - 'since': Resend events after 'after_timestamp' (requires after_timestamp)
            - None: Don't resend, just subscribe to new events
        after_timestamp: Required when resend_mode='since'. Events with
            timestamp >= this value will be sent. Timestamps are interpreted in
            server local time. Timezone-aware datetimes are converted to server
            timezone. Enables efficient bi-directional loading where REST fetches
            historical events and WebSocket handles events after a specific point.
        resend_all: DEPRECATED. Use resend_mode='all' instead. Kept for
            backward compatibility - if True and resend_mode is None, behaves
            as resend_mode='all'.
    """
    if not await _accept_authenticated_websocket(websocket, session_api_key):
        return

    logger.info(f"Event Websocket Connected: {conversation_id}")
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        logger.warning(f"Converation not found: {conversation_id}")
        await websocket.close(code=4004, reason="Conversation not found")
        return

    subscriber_id = await event_service.subscribe_to_events(
        _WebSocketSubscriber(websocket)
    )

    # Determine effective resend mode (handle deprecated resend_all)
    effective_mode = resend_mode
    if effective_mode is None and resend_all:
        logger.warning(
            "resend_all is deprecated, use resend_mode='all' instead: "
            f"{conversation_id}"
        )
        effective_mode = "all"

    # Normalize timezone-aware datetimes to server timezone
    normalized_after_timestamp = (
        normalize_datetime_to_server_timezone(after_timestamp)
        if after_timestamp
        else None
    )

    try:
        # Resend existing events based on mode
        if effective_mode == "all":
            logger.info(f"Resending all events: {conversation_id}")
            async for event in page_iterator(event_service.search_events):
                await _send_event(event, websocket)
        elif effective_mode == "since":
            if not normalized_after_timestamp:
                logger.warning(
                    f"resend_mode='since' requires after_timestamp, "
                    f"no events will be resent: {conversation_id}"
                )
            else:
                logger.info(
                    f"Resending events since {normalized_after_timestamp}: "
                    f"{conversation_id}"
                )
                async for event in page_iterator(
                    event_service.search_events,
                    timestamp__gte=normalized_after_timestamp,
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

                # For MCP errors send error event to client before closing
                if isinstance(e, MCPError):
                    try:
                        error_event = ConversationErrorEvent(
                            source="environment",
                            code="MCP_CONNECTION_ERROR",
                            detail=str(e),
                        )
                        await _send_event(error_event, websocket)
                    except Exception:
                        pass  # Websocket already broken: continue to raise original error
                    raise

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
    resend_mode: Annotated[
        Literal["all"] | None,
        Query(
            description=(
                "Mode for resending historical events on connect. "
                "'all' sends all events."
            )
        ),
    ] = None,
    # Deprecated parameter - kept for backward compatibility
    resend_all: Annotated[
        bool,
        Query(
            include_in_schema=False,
            deprecated=True,
        ),
    ] = False,
):
    """WebSocket endpoint for bash events.

    Args:
        websocket: The WebSocket connection.
        session_api_key: Optional API key for authentication.
        resend_mode: Mode for resending historical events on connect.
            - 'all': Resend all existing bash events
            - None: Don't resend, just subscribe to new events
        resend_all: DEPRECATED. Use resend_mode='all' instead.
    """
    if not await _accept_authenticated_websocket(websocket, session_api_key):
        return

    logger.info("Bash Websocket Connected")
    subscriber_id = await bash_event_service.subscribe_to_events(
        _BashWebSocketSubscriber(websocket)
    )

    # Determine effective resend mode (handle deprecated resend_all)
    effective_mode = resend_mode
    if effective_mode is None and resend_all:
        logger.warning("resend_all is deprecated, use resend_mode='all' instead")
        effective_mode = "all"

    try:
        # Resend all existing events if requested
        if effective_mode == "all":
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
