"""
WebSocket endpoints for OpenHands SDK.

These endpoints are separate from the main API routes to handle WebSocket-specific
authentication using query parameters instead of headers, since browsers cannot
send custom HTTP headers directly with WebSocket connections.
"""

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import (
    get_bash_event_service_ws,
    get_conversation_service_ws,
    websocket_session_api_key_dependency_ws,
)
from openhands.agent_server.models import BashEventBase, ExecuteBashRequest
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import Event, Message


# Back-compat shim for older tests that patch this symbol
get_conversation_service = get_conversation_service_ws

# Pure DI: rely exclusively on FastAPI dependencies


sockets_router = APIRouter(prefix="/sockets", tags=["WebSockets"])
logger = logging.getLogger(__name__)


@sockets_router.websocket("/events/{conversation_id}")
async def events_socket(
    conversation_id: UUID,
    websocket: WebSocket,
    resend_all: Annotated[bool, Query()] = False,
    conv_svc: ConversationService = Depends(get_conversation_service_ws),
    _auth: None = Depends(websocket_session_api_key_dependency_ws),
):
    """WebSocket endpoint for conversation events."""
    await websocket.accept()
    logger.info(f"Event Websocket Connected: {conversation_id}")
    event_service = await conv_svc.get_event_service(conversation_id)
    if event_service is None:
        logger.warning(f"Converation not found: {conversation_id}")
        await websocket.close(code=4004, reason="Conversation not found")
        return

    subscriber_id = await event_service.subscribe_to_events(
        _WebSocketSubscriber(websocket)
    )

    try:
        # Resend all existing events if requested
        if resend_all:
            logger.info(f"Resending events: {conversation_id}")
            page_id = None
            while True:
                page = await event_service.search_events(page_id=page_id)
                for event in page.items:
                    await _send_event(event, websocket)
                page_id = page.next_page_id
                if not page_id:
                    break

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
                if isinstance(e, (RuntimeError, ConnectionError)):
                    raise
    finally:
        await event_service.unsubscribe_from_events(subscriber_id)


@sockets_router.websocket("/bash-events")
async def bash_events_socket(
    websocket: WebSocket,
    resend_all: Annotated[bool, Query()] = False,
    bash_event_service: BashEventService = Depends(get_bash_event_service_ws),
    _auth: None = Depends(websocket_session_api_key_dependency_ws),
):
    """WebSocket endpoint for bash events."""
    await websocket.accept()
    logger.info("Bash Websocket Connected")
    subscriber_id = await bash_event_service.subscribe_to_events(
        _BashWebSocketSubscriber(websocket)
    )
    try:
        # Resend all existing events if requested
        if resend_all:
            logger.info("Resending bash events")
            page_id = None
            while True:
                page = await bash_event_service.search_bash_events(page_id=page_id)
                for event in page.items:
                    await _send_bash_event(event, websocket)
                page_id = page.next_page_id
                if not page_id:
                    break

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
                if isinstance(e, (RuntimeError, ConnectionError)):
                    raise
    finally:
        await bash_event_service.unsubscribe_from_events(subscriber_id)


async def _send_event(event: Event, websocket: WebSocket):
    try:
        dumped = event.model_dump()
        await websocket.send_json(dumped)
    except Exception:
        logger.exception("error_sending_event:{event}", stack_info=True)


@dataclass
class _WebSocketSubscriber(Subscriber):
    """WebSocket subscriber for conversation events."""

    websocket: WebSocket

    async def __call__(self, event: Event):
        await _send_event(event, self.websocket)


async def _send_bash_event(event: BashEventBase, websocket: WebSocket):
    try:
        dumped = event.model_dump()
        await websocket.send_json(dumped)
    except Exception:
        logger.exception("error_sending_event:{event}", stack_info=True)


@dataclass
class _BashWebSocketSubscriber(Subscriber[BashEventBase]):
    """WebSocket subscriber for bash events."""

    websocket: WebSocket

    async def __call__(self, event: BashEventBase):
        await _send_bash_event(event, self.websocket)
