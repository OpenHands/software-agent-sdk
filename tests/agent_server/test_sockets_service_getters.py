"""The websocket handlers take their services from the server context.

``ConversationService`` and ``BashEventService`` belong to the context of the
app a socket is connected to (installed by the lifespan, or by
``POST /api/init`` in deferred-init mode). There is no process-wide service to
fall back to: a context whose services have not started refuses the socket.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, WebSocketDisconnect, WebSocketException, status

import openhands.agent_server.sockets as sockets_mod
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import (
    get_conversation_service,
    get_server_bash_event_service,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.server_context import ServerContext, build_server_context


def _context(
    config: Config | None = None,
    *,
    conversation_service: ConversationService | None = None,
    bash_event_service: BashEventService | None = None,
) -> ServerContext:
    base = build_server_context(config or Config(session_api_keys=[]))
    return ServerContext(
        config=base.config,
        stores=base.stores,
        conversation_registry=base.conversation_registry,
        conversation_service=conversation_service,
        bash_event_service=bash_event_service,
    )


def _make_ws(context: ServerContext, scope_type: str = "websocket") -> MagicMock:
    """Build a mock connection whose app runs with ``context``."""
    ws = MagicMock()
    ws.app.state = SimpleNamespace(server_context=context)
    ws.scope = {"type": scope_type}
    return ws


@pytest.fixture
def conv_service():
    return MagicMock(spec=ConversationService)


@pytest.fixture
def bash_service():
    return MagicMock(spec=BashEventService)


# -- get_conversation_service --


def test_get_conversation_service_returns_the_context_service(conv_service):
    ws = _make_ws(_context(conversation_service=conv_service))
    assert get_conversation_service(ws) is conv_service


def test_get_conversation_service_refuses_a_socket_without_a_started_service():
    """A dormant context has no service, and nothing stands in for it."""
    ws = _make_ws(_context())
    with pytest.raises(WebSocketException) as excinfo:
        get_conversation_service(ws)
    assert excinfo.value.code == status.WS_1013_TRY_AGAIN_LATER


def test_get_conversation_service_answers_503_to_a_request_without_a_service():
    request = _make_ws(_context(), scope_type="http")
    with pytest.raises(HTTPException) as excinfo:
        get_conversation_service(request)
    assert excinfo.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# -- get_server_bash_event_service --


def test_get_bash_event_service_returns_the_context_service(bash_service):
    ws = _make_ws(_context(bash_event_service=bash_service))
    assert get_server_bash_event_service(ws) is bash_service


def test_get_bash_event_service_refuses_a_socket_without_a_started_service():
    ws = _make_ws(_context())
    with pytest.raises(WebSocketException) as excinfo:
        get_server_bash_event_service(ws)
    assert excinfo.value.code == status.WS_1013_TRY_AGAIN_LATER


def test_get_bash_event_service_answers_503_to_a_request_without_a_service():
    request = _make_ws(_context(), scope_type="http")
    with pytest.raises(HTTPException) as excinfo:
        get_server_bash_event_service(request)
    assert excinfo.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_an_app_without_a_context_is_a_programming_error():
    """Nothing is built behind the caller's back when no context is installed."""
    ws = MagicMock()
    ws.app.state = SimpleNamespace()
    with pytest.raises(RuntimeError, match="no ServerContext"):
        get_conversation_service(ws)


# -- End-to-end: WebSocket handlers use the context's services --


@pytest.mark.asyncio
async def test_events_socket_uses_the_context_conversation_service():
    """events_socket must use the service of the context (set by /api/init)."""
    from openhands.agent_server.sockets import events_socket

    mock_event_svc = MagicMock(spec=EventService)
    mock_event_svc.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_event_svc.unsubscribe_from_events = AsyncMock(return_value=True)

    per_app_conv_svc = MagicMock(spec=ConversationService)
    per_app_conv_svc.get_event_service = AsyncMock(return_value=mock_event_svc)

    ws = _make_ws(_context(conversation_service=per_app_conv_svc))
    ws.accept = AsyncMock()
    ws.receive_text = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}

    await events_socket(uuid4(), ws, session_api_key=None)

    per_app_conv_svc.get_event_service.assert_called_once()
    mock_event_svc.subscribe_to_events.assert_called_once()
    mock_event_svc.unsubscribe_from_events.assert_called_once()


@pytest.mark.asyncio
async def test_bash_events_socket_uses_the_context_bash_event_service():
    """bash_events_socket must use the service of the context (set by /api/init)."""
    from openhands.agent_server.sockets import bash_events_socket

    per_app_bash_svc = MagicMock(spec=BashEventService)
    per_app_bash_svc.subscribe_to_events = AsyncMock(return_value=uuid4())
    per_app_bash_svc.unsubscribe_from_events = AsyncMock(return_value=True)

    ws = _make_ws(_context(bash_event_service=per_app_bash_svc))
    ws.accept = AsyncMock()
    ws.receive_text = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}

    await bash_events_socket(ws, session_api_key=None)

    per_app_bash_svc.subscribe_to_events.assert_called_once()
    per_app_bash_svc.unsubscribe_from_events.assert_called_once()


@pytest.mark.parametrize("send_fails", [False, True])
async def test_events_socket_preserves_diagnostics_without_extra_stack(
    caplog, send_fails
):
    event_service = MagicMock(spec=EventService)
    subscriber_id = uuid4()
    event_service.subscribe_to_events = AsyncMock(return_value=subscriber_id)
    event_service.unsubscribe_from_events = AsyncMock()
    service = MagicMock(spec=ConversationService)
    service.get_event_service = AsyncMock(return_value=event_service)
    ws = _make_ws(_context(conversation_service=service))
    ws.headers = {}
    ws.accept = AsyncMock()
    ws.receive_json = AsyncMock(
        side_effect=[ValueError("invalid input"), WebSocketDisconnect()]
    )
    ws.send_json = AsyncMock(side_effect=RuntimeError("closed") if send_fails else None)
    ws.close = AsyncMock()
    caplog.set_level("DEBUG", logger=sockets_mod.__name__)

    await sockets_mod.events_socket(uuid4(), ws)

    payload = ws.send_json.call_args.args[0]
    assert payload["code"] == "ValueError"
    assert payload["detail"] == "invalid input"
    event_service.unsubscribe_from_events.assert_awaited_once_with(subscriber_id)
    records = [
        record for record in caplog.records if record.name == sockets_mod.__name__
    ]
    assert records
    assert all(record.stack_info is None for record in records)
    if send_fails:
        assert all(record.exc_info is None for record in records)
        assert any("Event websocket disconnected" in r.getMessage() for r in records)
    else:
        error_record = next(
            r for r in records if r.getMessage().startswith("error_in_subscription")
        )
        assert error_record.exc_info is not None
        assert isinstance(error_record.exc_info[1], ValueError)
        assert str(error_record.exc_info[1]) == "invalid input"
        assert error_record.exc_info[2] is not None
