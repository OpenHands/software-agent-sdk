"""Tests for first-message WebSocket authentication in sockets.py."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from openhands.agent_server.sockets import _accept_authenticated_websocket


def _make_mock_websocket(*, headers=None):
    """Build a mock WebSocket with configurable query params and headers."""
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.receive_text = AsyncMock()
    ws.receive_json = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = headers or {}
    return ws


# -- No auth configured (empty session_api_keys) --


@pytest.mark.asyncio
async def test_no_auth_configured_accepts_immediately():
    ws = _make_mock_websocket()
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = []
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is True
    ws.accept.assert_called_once()
    ws.receive_text.assert_not_called()


# -- Legacy query param auth (deprecated) --


@pytest.mark.asyncio
async def test_legacy_query_param_valid_key():
    ws = _make_mock_websocket()
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(
            ws, session_api_key="sk-oh-valid"
        )

    assert result is True
    ws.accept.assert_called_once()
    ws.receive_text.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_query_param_invalid_key():
    ws = _make_mock_websocket()
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(
            ws, session_api_key="sk-oh-wrong"
        )

    assert result is False
    ws.close.assert_called_once_with(code=4001, reason="Authentication failed")
    ws.accept.assert_not_called()


# -- Legacy header auth (deprecated) --


@pytest.mark.asyncio
async def test_legacy_header_valid_key():
    ws = _make_mock_websocket(headers={"x-session-api-key": "sk-oh-valid"})
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is True
    ws.accept.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_header_invalid_key():
    ws = _make_mock_websocket(headers={"x-session-api-key": "sk-oh-wrong"})
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False
    ws.close.assert_called_once_with(code=4001, reason="Authentication failed")


# -- First-message auth --


@pytest.mark.asyncio
async def test_first_message_auth_valid_key():
    ws = _make_mock_websocket()
    ws.receive_text.return_value = json.dumps(
        {"type": "auth", "session_api_key": "sk-oh-valid"}
    )
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is True
    ws.accept.assert_called_once()
    ws.receive_text.assert_called_once()


@pytest.mark.asyncio
async def test_first_message_auth_invalid_key():
    ws = _make_mock_websocket()
    ws.receive_text.return_value = json.dumps(
        {"type": "auth", "session_api_key": "sk-oh-wrong"}
    )
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False
    ws.accept.assert_called_once()  # accepted before reading first message
    ws.close.assert_called_once_with(code=4001, reason="Authentication failed")


@pytest.mark.asyncio
async def test_first_message_auth_wrong_type_field():
    ws = _make_mock_websocket()
    ws.receive_text.return_value = json.dumps(
        {"type": "message", "session_api_key": "sk-oh-valid"}
    )
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False


@pytest.mark.asyncio
async def test_first_message_auth_missing_key_field():
    ws = _make_mock_websocket()
    ws.receive_text.return_value = json.dumps({"type": "auth"})
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False


@pytest.mark.asyncio
async def test_first_message_auth_malformed_json():
    ws = _make_mock_websocket()
    ws.receive_text.return_value = "not json at all"
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False
    ws.close.assert_called_once_with(code=4001, reason="Authentication failed")


@pytest.mark.asyncio
async def test_first_message_auth_client_disconnects():
    ws = _make_mock_websocket()
    ws.receive_text.side_effect = WebSocketDisconnect()
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False


@pytest.mark.asyncio
async def test_first_message_auth_timeout():
    ws = _make_mock_websocket()

    async def slow_receive():
        await asyncio.sleep(60)

    ws.receive_text.side_effect = slow_receive

    with (
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        patch(
            "openhands.agent_server.sockets._FIRST_MESSAGE_AUTH_TIMEOUT_SECONDS", 0.05
        ),
    ):
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(ws, session_api_key=None)

    assert result is False
    ws.close.assert_called_once_with(code=4001, reason="Authentication failed")


# -- End-to-end: first-message auth through events_socket --


@pytest.mark.asyncio
async def test_events_socket_first_message_auth_e2e():
    """First-message auth works end-to-end through the events_socket endpoint."""
    from openhands.agent_server.event_service import EventService
    from openhands.agent_server.sockets import events_socket

    ws = _make_mock_websocket()
    # Auth via receive_text, then receive_json raises disconnect.
    ws.receive_text.return_value = json.dumps(
        {"type": "auth", "session_api_key": "sk-oh-valid"}
    )
    ws.receive_json.side_effect = WebSocketDisconnect()

    mock_event_service = MagicMock(spec=EventService)
    mock_event_service.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_event_service.unsubscribe_from_events = AsyncMock(return_value=True)

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        await events_socket(uuid4(), ws, session_api_key=None)

    ws.accept.assert_called_once()
    mock_event_service.subscribe_to_events.assert_called_once()
    mock_event_service.unsubscribe_from_events.assert_called_once()


@pytest.mark.asyncio
async def test_events_socket_first_message_auth_rejected():
    """events_socket returns early when first-message auth fails."""
    from openhands.agent_server.sockets import events_socket

    ws = _make_mock_websocket()
    ws.receive_text.return_value = json.dumps(
        {"type": "auth", "session_api_key": "sk-oh-wrong"}
    )

    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]

        await events_socket(uuid4(), ws, session_api_key=None)

    ws.accept.assert_called_once()
    # Should not proceed to subscribe
    ws.receive_json.assert_not_called()
