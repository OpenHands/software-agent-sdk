"""Tests for websocket functionality in event_router.py"""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import EventPage
from openhands.agent_server.sockets import _WebSocketSubscriber
from openhands.sdk import Message
from openhands.sdk.event import Event
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm.message import TextContent


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket for testing."""
    websocket = MagicMock()
    websocket.accept = AsyncMock()
    websocket.receive_json = AsyncMock()
    websocket.send_json = AsyncMock()
    websocket.close = AsyncMock()
    websocket.application_state = MagicMock()
    return websocket


@pytest.fixture
def mock_event_service():
    """Create a mock EventService for testing."""
    service = MagicMock(spec=EventService)
    service.subscribe_to_events = AsyncMock(return_value=uuid4())
    service.unsubscribe_from_events = AsyncMock(return_value=True)
    service.send_message = AsyncMock()
    service.search_events = AsyncMock()
    return service


@pytest.fixture
def sample_conversation_id():
    """Return a sample conversation ID."""
    return uuid4()


@pytest.mark.asyncio
async def test_websocket_subscriber_call_success(mock_websocket):
    """Test successful event sending through WebSocket subscriber."""
    subscriber = _WebSocketSubscriber(websocket=mock_websocket)
    event = MessageEvent(
        id="test_event",
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="test")]),
    )

    await subscriber(event)

    mock_websocket.send_json.assert_called_once()
    call_args = mock_websocket.send_json.call_args[0][0]
    assert call_args["id"] == "test_event"


@pytest.mark.asyncio
async def test_websocket_subscriber_call_exception(mock_websocket):
    """Test exception handling in WebSocket subscriber."""
    mock_websocket.send_json.side_effect = Exception("Connection error")
    subscriber = _WebSocketSubscriber(websocket=mock_websocket)
    event = MessageEvent(
        id="test_event",
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="test")]),
    )

    # Should not raise exception, just log it
    await subscriber(event)

    mock_websocket.send_json.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_disconnect_breaks_loop(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that WebSocketDisconnect exception breaks the loop."""
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id, mock_websocket, session_api_key=None
        )

    mock_event_service.unsubscribe_from_events.assert_called()


@pytest.mark.asyncio
async def test_websocket_no_double_unsubscription(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that unsubscription only happens once even with disconnect."""
    subscriber_id = uuid4()
    mock_event_service.subscribe_to_events.return_value = subscriber_id
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id, mock_websocket, session_api_key=None
        )

    assert mock_event_service.unsubscribe_from_events.call_count == 1
    mock_event_service.unsubscribe_from_events.assert_called_with(subscriber_id)


@pytest.mark.asyncio
async def test_websocket_general_exception_continues_loop(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that general exceptions don't break the loop immediately."""
    call_count = 0

    def side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("Some error")
        elif call_count == 2:
            raise WebSocketDisconnect()

    mock_websocket.receive_json.side_effect = side_effect

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id, mock_websocket, session_api_key=None
        )

    assert mock_websocket.receive_json.call_count == 2
    mock_event_service.unsubscribe_from_events.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_successful_message_processing(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test successful message processing before disconnect."""
    message_data = {"role": "user", "content": "Hello"}
    call_count = 0

    def side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return message_data
        else:
            raise WebSocketDisconnect()

    mock_websocket.receive_json.side_effect = side_effect

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id, mock_websocket, session_api_key=None
        )

    mock_event_service.send_message.assert_called_once()
    assert mock_websocket.receive_json.call_count == 2


@pytest.mark.asyncio
async def test_websocket_unsubscribe_in_finally_when_no_disconnect(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that unsubscribe is called even when there's no WebSocketDisconnect."""
    mock_websocket.receive_json.side_effect = RuntimeError("Connection broken")

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        with pytest.raises(RuntimeError):
            await events_socket(
                sample_conversation_id, mock_websocket, session_api_key=None
            )

    mock_event_service.unsubscribe_from_events.assert_called_once()


@pytest.mark.asyncio
async def test_resend_all_false_no_resend(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that resend_all=False doesn't trigger event resend."""
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id,
            mock_websocket,
            session_api_key=None,
            resend_all=False,
        )

    mock_event_service.search_events.assert_not_called()


@pytest.mark.asyncio
async def test_resend_all_true_resends_events(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that resend_all=True resends all existing events."""
    mock_events = [
        MessageEvent(
            id="event1",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="Hello")]),
        ),
        MessageEvent(
            id="event2",
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text="Hi")]),
        ),
    ]
    mock_event_page = EventPage(items=cast(list[Event], mock_events), next_page_id=None)
    mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id,
            mock_websocket,
            session_api_key=None,
            resend_all=True,
        )

    mock_event_service.search_events.assert_called_once_with(
        page_id=None, timestamp__gte=None
    )
    assert mock_websocket.send_json.call_count == 2
    sent_events = [call[0][0] for call in mock_websocket.send_json.call_args_list]
    assert sent_events[0]["id"] == "event1"
    assert sent_events[1]["id"] == "event2"


@pytest.mark.asyncio
async def test_after_timestamp_passed_to_search_events(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that after_timestamp is normalized and passed to search_events."""
    mock_events = [
        MessageEvent(
            id="event1",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="Hello")]),
        ),
    ]
    mock_event_page = EventPage(items=cast(list[Event], mock_events), next_page_id=None)
    mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    # Use a naive timestamp (as would typically come from REST API response)
    test_timestamp = datetime(2024, 1, 15, 10, 30, 0)

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id,
            mock_websocket,
            session_api_key=None,
            resend_all=True,
            after_timestamp=test_timestamp,
        )

    mock_event_service.search_events.assert_called_once_with(
        page_id=None, timestamp__gte=test_timestamp
    )


@pytest.mark.asyncio
async def test_after_timestamp_timezone_aware_is_normalized(
    mock_websocket, mock_event_service, sample_conversation_id
):
    """Test that timezone-aware timestamps are normalized to naive server time."""
    mock_events = [
        MessageEvent(
            id="event1",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="Hello")]),
        ),
    ]
    mock_event_page = EventPage(items=cast(list[Event], mock_events), next_page_id=None)
    mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()

    # Use a timezone-aware timestamp (UTC)
    test_timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    with (
        patch(
            "openhands.agent_server.sockets.conversation_service"
        ) as mock_conv_service,
        patch("openhands.agent_server.sockets.get_default_config") as mock_config,
    ):
        mock_config.return_value.session_api_keys = None
        mock_conv_service.get_event_service = AsyncMock(return_value=mock_event_service)

        from openhands.agent_server.sockets import events_socket

        await events_socket(
            sample_conversation_id,
            mock_websocket,
            session_api_key=None,
            resend_all=True,
            after_timestamp=test_timestamp,
        )

    # search_events should be called with the normalized timestamp
    mock_event_service.search_events.assert_called_once()
    call_args = mock_event_service.search_events.call_args
    passed_timestamp = call_args.kwargs["timestamp__gte"]
    # The timestamp should be naive (no tzinfo)
    assert passed_timestamp is not None
    assert passed_timestamp.tzinfo is None
    # It should represent the same instant in time (converted to local)
    expected = test_timestamp.astimezone(None).replace(tzinfo=None)
    assert passed_timestamp == expected
