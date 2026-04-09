"""
WebSocket endpoints for OpenHands SDK.

These endpoints are separate from the main API routes to handle WebSocket-specific
authentication.  Three auth methods are supported (highest to lowest precedence):

1. **First-message auth** (recommended): The client sends
   ``{"type": "auth", "session_api_key": "..."}`` as the very first WebSocket
   frame after the connection opens.  This keeps tokens out of URLs and
   therefore out of reverse-proxy / load-balancer access logs.
2. Query parameter ``session_api_key`` — deprecated, kept for backwards compat.
3. ``X-Session-API-Key`` header — for non-browser clients.
"""

import asyncio
import json
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
from openhands.agent_server.models import (
    BashError,
    BashEventBase,
    ExecuteBashRequest,
    ServerErrorEvent,
)
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import Event, Message
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


# Give clients 10 seconds to send auth frame after connection opens.
# This balances security (don't hold connections indefinitely) with
# accommodating slow networks and client startup time.
_FIRST_MESSAGE_AUTH_TIMEOUT_SECONDS = 10


async def _accept_authenticated_websocket(
    websocket: WebSocket,
    session_api_key: str | None,
) -> bool:
    """Authenticate and accept the socket, or close with an auth error.

    Authentication is attempted in the following order:

    1. Query parameter / header (legacy, deprecated).
    2. First-message auth — the client sends
       ``{"type": "auth", "session_api_key": "..."}`` as the first frame.

    The WebSocket is always *accepted* before first-message auth is attempted
    because raw WebSocket requires ``accept()`` before any frames can be read.
    """
    config = _get_config(websocket)
    resolved_key = _resolve_websocket_session_api_key(websocket, session_api_key)

    # No auth configured — accept unconditionally.
    if not config.session_api_keys:
        await websocket.accept()
        return True

    # Legacy path: key supplied via query param or header.
    if resolved_key is not None:
        if resolved_key in config.session_api_keys:
            logger.warning(
                "session_api_key passed via query param or header is deprecated. "
                "Use first-message auth instead."
            )
            await websocket.accept()
            return True
        logger.warning("WebSocket authentication failed: invalid API key")
        await websocket.close(code=4001, reason="Authentication failed")
        return False

    # First-message auth: we must accept() before reading frames because the
    # WebSocket protocol requires the handshake to complete first.  The legacy
    # path above can reject *before* accepting (close on an un-accepted socket
    # sends an HTTP 403-style response), but here we need to read a frame.
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=_FIRST_MESSAGE_AUTH_TIMEOUT_SECONDS,
        )
        data = json.loads(raw)
    except TimeoutError:
        logger.warning(
            "WebSocket first-message auth failed: timeout waiting for auth frame"
        )
        await _safe_close_websocket(
            websocket, code=4001, reason="Authentication failed"
        )
        return False
    except json.JSONDecodeError:
        logger.warning("WebSocket first-message auth failed: malformed JSON")
        await _safe_close_websocket(
            websocket, code=4001, reason="Authentication failed"
        )
        return False
    except WebSocketDisconnect:
        logger.warning("WebSocket first-message auth failed: client disconnected")
        await _safe_close_websocket(
            websocket, code=4001, reason="Authentication failed"
        )
        return False

    if not isinstance(data, dict):
        logger.warning(
            "WebSocket first-message auth failed: payload is not a JSON object"
        )
        await _safe_close_websocket(
            websocket, code=4001, reason="Authentication failed"
        )
        return False
    if data.get("type") != "auth":
        logger.warning("WebSocket first-message auth failed: wrong message type")
        await _safe_close_websocket(
            websocket, code=4001, reason="Authentication failed"
        )
        return False
    if data.get("session_api_key") not in config.session_api_keys:
        logger.warning("WebSocket first-message auth failed: invalid API key")
        await _safe_close_websocket(
            websocket, code=4001, reason="Authentication failed"
        )
        return False

    logger.info("WebSocket authenticated via first-message auth")
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
                logger.info("Event websocket disconnected")
                return
            except Exception as e:
                # Something went wrong - Tell the client so they can handle it
                try:
                    error_event = ServerErrorEvent(
                        source="environment",
                        code=e.__class__.__name__,
                        detail=str(e),
                    )
                    dumped = error_event.model_dump(mode="json")
                    await websocket.send_json(dumped)
                    # Log after - if send event raises an error logging is handled
                    # in the except block
                    logger.exception("error_in_subscription", stack_info=True)
                except Exception:
                    # Sending the error event failed - likely a closed socket
                    logger.info("Event websocket disconnected")
                    logger.debug("error_sending_error", exc_info=True, stack_info=True)
                    await _safe_close_websocket(websocket)
                    return
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
                logger.info("Bash websocket disconnected")
                return
            except Exception as e:
                # Something went wrong - Tell the client so they can handle it
                try:
                    error_event = BashError(
                        code=e.__class__.__name__,
                        detail=str(e),
                    )
                    dumped = error_event.model_dump(mode="json")
                    await websocket.send_json(dumped)
                    # Log after - if send event raises an error logging is handled
                    # in the except block
                    logger.exception(
                        "error_in_bash_event_subscription", stack_info=True
                    )
                except Exception:
                    # Sending the error event failed - likely a closed socket
                    logger.info("Base websocket disconnected")
                    logger.debug(
                        "error_sending_bash_error", exc_info=True, stack_info=True
                    )
                    await _safe_close_websocket(websocket)
                    return
    finally:
        await bash_event_service.unsubscribe_from_events(subscriber_id)


async def _send_event(event: Event, websocket: WebSocket):
    try:
        dumped = event.model_dump(mode="json")
        await websocket.send_json(dumped)
    except Exception:
        logger.exception("error_sending_event: %r", event, stack_info=True)


async def _safe_close_websocket(
    websocket: WebSocket,
    code: int = 1000,
    reason: str = "Connection closed",
):
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        # WebSocket may already be closed or in inconsistent state
        logger.debug("WebSocket close failed (may already be closed)")


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
