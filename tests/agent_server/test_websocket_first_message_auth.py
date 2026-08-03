"""Tests for first-message WebSocket authentication in sockets.py."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from openhands.agent_server.sockets import (
    _accept_authenticated_websocket,
    _is_auth_control_message,
)


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


@pytest.mark.asyncio
async def test_legacy_query_param_takes_precedence_over_first_message():
    """When both query param and first-message auth could apply, query param wins."""
    ws = _make_mock_websocket()
    ws.receive_text.return_value = json.dumps(
        {"type": "auth", "session_api_key": "sk-oh-different"}
    )
    with patch("openhands.agent_server.sockets.get_default_config") as mock_config:
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        result = await _accept_authenticated_websocket(
            ws, session_api_key="sk-oh-valid"
        )

    assert result is True
    ws.accept.assert_called_once()
    # Should NOT read first message because query param already authenticated.
    ws.receive_text.assert_not_called()


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
async def test_events_socket_ignores_redundant_auth_control_frame():
    """A redundant ``{"type": "auth", ...}`` frame after legacy auth is ignored.

    Regression for issue #3127: mixed-mode clients can authenticate via the
    legacy query param / header and *also* send a first-message auth frame.
    The post-auth receive loop must skip that frame instead of validating
    it as a ``Message`` (which fails on the missing ``role`` field and
    emits a noisy ``ServerErrorEvent``).
    """
    from openhands.agent_server.event_service import EventService
    from openhands.agent_server.sockets import events_socket

    ws = _make_mock_websocket()
    # First frame on the post-auth loop is the redundant auth control
    # message; second frame is a real user message; third closes the loop.
    real_user_message = {"type": "auth", "role": "user", "content": []}
    ws.receive_json.side_effect = [
        {"type": "auth", "session_api_key": "sk-oh-valid"},
        real_user_message,
        WebSocketDisconnect(),
    ]

    mock_event_service = MagicMock(spec=EventService)
    mock_event_service.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_event_service.unsubscribe_from_events = AsyncMock(return_value=True)
    mock_event_service.send_message = AsyncMock()

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = ["sk-oh-valid"]
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        # Authenticate via legacy query param so receive_text is never called.
        await events_socket(uuid4(), ws, session_api_key="sk-oh-valid")

    # No ServerErrorEvent should be emitted for the auth control frame.
    ws.send_json.assert_not_called()
    # send_message is only called for the real user message, exactly once.
    assert mock_event_service.send_message.await_count == 1
    sent_message = mock_event_service.send_message.await_args.args[0]
    assert sent_message.role == "user"


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_api_keys", "session_api_key", "headers"),
    [
        pytest.param([], None, {}, id="no-auth"),
        pytest.param(
            ["sk-oh-valid"],
            "sk-oh-valid",
            {},
            id="query-auth",
        ),
        pytest.param(
            ["sk-oh-valid"],
            None,
            {"x-session-api-key": "sk-oh-valid"},
            id="header-auth",
        ),
    ],
)
async def test_bash_socket_ignores_redundant_auth_before_command(
    session_api_keys,
    session_api_key,
    headers,
    caplog,
):
    from openhands.agent_server.bash_service import BashEventService
    from openhands.agent_server.sockets import bash_events_socket

    secret = "sk-oh-" + "z" * 64
    caplog.set_level(logging.DEBUG)
    ws = _make_mock_websocket(headers=headers)
    ws.receive_json.side_effect = [
        {"type": "auth", "session_api_key": secret},
        {"type": "auth", "command": "printf queued-command"},
        WebSocketDisconnect(),
    ]
    mock_bash_service = MagicMock(spec=BashEventService)
    mock_bash_service.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_bash_service.unsubscribe_from_events = AsyncMock(return_value=True)
    mock_bash_service.start_bash_command = AsyncMock()

    with (
        patch(
            "openhands.agent_server.sockets.bash_event_service",
            mock_bash_service,
        ),
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = session_api_keys
        await bash_events_socket(
            ws,
            session_api_key=session_api_key,
            resend_mode=None,
            resend_all=False,
        )

    mock_bash_service.start_bash_command.assert_awaited_once()
    request = mock_bash_service.start_bash_command.await_args.args[0]
    assert request.command == "printf queued-command"
    ws.send_json.assert_not_called()
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_bash_socket_validation_error_log_omits_payload(caplog):
    from openhands.agent_server.bash_service import BashEventService
    from openhands.agent_server.sockets import bash_events_socket

    secret = "ghp_" + "j" * 36
    caplog.set_level(logging.DEBUG, logger="openhands.agent_server.sockets")
    ws = _make_mock_websocket()
    ws.receive_json.side_effect = [
        {"command": {"credential": secret}},
        WebSocketDisconnect(),
    ]
    mock_bash_service = MagicMock(spec=BashEventService)
    mock_bash_service.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_bash_service.unsubscribe_from_events = AsyncMock(return_value=True)
    mock_bash_service.start_bash_command = AsyncMock()

    with (
        patch(
            "openhands.agent_server.sockets.bash_event_service",
            mock_bash_service,
        ),
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = []
        await bash_events_socket(
            ws,
            session_api_key=None,
            resend_mode=None,
            resend_all=False,
        )

    mock_bash_service.start_bash_command.assert_not_awaited()
    mock_bash_service.unsubscribe_from_events.assert_awaited_once()
    ws.send_json.assert_awaited_once()
    assert ws.send_json.await_args.args[0]["code"] == "ValidationError"
    assert "error_in_bash_event_subscription" in caplog.text
    assert "ValidationError" in caplog.text
    assert secret not in caplog.text
    error_record = next(
        record
        for record in caplog.records
        if "error_in_bash_event_subscription" in record.getMessage()
    )
    assert error_record.exc_info is None
    assert error_record.stack_info is None


@pytest.mark.asyncio
async def test_bash_socket_error_send_failure_log_omits_exceptions(caplog):
    from openhands.agent_server.bash_service import BashEventService
    from openhands.agent_server.sockets import bash_events_socket

    payload_secret = "ghp_" + "k" * 36
    send_secret = "sk-oh-" + "l" * 64
    caplog.set_level(logging.DEBUG, logger="openhands.agent_server.sockets")
    ws = _make_mock_websocket()
    ws.receive_json.return_value = {"command": {"credential": payload_secret}}
    ws.send_json.side_effect = RuntimeError(f"send failed with {send_secret}")
    mock_bash_service = MagicMock(spec=BashEventService)
    mock_bash_service.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_bash_service.unsubscribe_from_events = AsyncMock(return_value=True)
    mock_bash_service.start_bash_command = AsyncMock()

    with (
        patch(
            "openhands.agent_server.sockets.bash_event_service",
            mock_bash_service,
        ),
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = []
        await bash_events_socket(
            ws,
            session_api_key=None,
            resend_mode=None,
            resend_all=False,
        )

    mock_bash_service.start_bash_command.assert_not_awaited()
    mock_bash_service.unsubscribe_from_events.assert_awaited_once()
    ws.close.assert_awaited_once()
    assert "error_sending_bash_error" in caplog.text
    assert "ValidationError" in caplog.text
    assert "RuntimeError" in caplog.text
    assert payload_secret not in caplog.text
    assert send_secret not in caplog.text
    error_record = next(
        record
        for record in caplog.records
        if "error_sending_bash_error" in record.getMessage()
    )
    assert error_record.exc_info is None
    assert error_record.stack_info is None


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(
            {"type": "auth", "session_api_key": "sk-oh-valid"},
            True,
            id="auth-frame",
        ),
        pytest.param({"type": "auth"}, True, id="auth-frame-without-key"),
        pytest.param(
            {"type": "auth", "command": "printf hi"},
            False,
            id="bash-payload",
        ),
        pytest.param(
            {"type": "auth", "role": "user"},
            False,
            id="message-payload",
        ),
        pytest.param(
            {"type": "auth", "unexpected": True},
            False,
            id="unknown-field",
        ),
        pytest.param({"type": "message"}, False, id="other-type"),
        pytest.param("auth", False, id="non-object"),
    ],
)
def test_auth_control_message_has_only_protocol_fields(data, expected):
    assert _is_auth_control_message(data) is expected
