"""Tests for WebSocketCallbackClient."""

import asyncio
import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import websockets
import websockets.frames

from openhands.sdk.conversation.impl.remote_conversation import WebSocketCallbackClient
from openhands.sdk.event.conversation_state import FULL_STATE_KEY
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, TextContent


@pytest.fixture
def mock_event():
    """Create a test event."""
    return MessageEvent(
        id="test-event-id",
        timestamp=datetime.now().isoformat(),
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Test message")]
        ),
    )


def test_websocket_client_lifecycle():
    """Test WebSocket client start/stop lifecycle with idempotency."""
    callback_events = []

    def test_callback(event):
        callback_events.append(event)

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=test_callback,
    )

    assert isinstance(client, WebSocketCallbackClient)

    with patch.object(client, "_run"):
        # Start the client
        client.start()
        assert client._thread is not None
        assert client._thread.daemon is True

        # Starting again should be idempotent
        original_thread = client._thread
        client.start()
        assert client._thread is original_thread

        # Stop the client
        client.stop()
        assert client._stop.is_set()
        assert client._thread is None


def test_websocket_client_error_resilience(mock_event):
    """Test that callback exceptions are logged but don't crash the client."""

    def failing_callback(event):
        raise ValueError("Test error")

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=failing_callback,
    )

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.logger"
    ) as mock_logger:
        try:
            client.callback(mock_event)
        except Exception:
            mock_logger.exception("ws_event_processing_error", stack_info=True)

        mock_logger.exception.assert_called_with(
            "ws_event_processing_error", stack_info=True
        )


def test_websocket_client_stop_timeout():
    """Test WebSocket client handles thread join timeout gracefully."""

    def noop_callback(event):
        pass

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=noop_callback,
    )

    # Mock thread that simulates delay
    mock_thread = MagicMock()
    mock_thread.join.side_effect = lambda timeout: time.sleep(0.1)
    client._thread = mock_thread

    start_time = time.time()
    client.stop()
    end_time = time.time()

    mock_thread.join.assert_called_with(timeout=5)
    assert end_time - start_time < 1.0
    assert client._thread is None


def test_websocket_client_callback_invocation(mock_event):
    """Test callback is invoked with events."""
    callback_events = []

    def test_callback(event):
        callback_events.append(event)

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=test_callback,
    )

    client.callback(mock_event)

    assert len(callback_events) == 1
    assert callback_events[0].id == mock_event.id


def test_websocket_client_url_encodes_api_key():
    """Test that API key special characters are URL-encoded in the WebSocket URL."""
    captured_urls = []

    class _MockAsyncContextManager:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            captured_urls.append(self.url)
            raise websockets.exceptions.ConnectionClosed(
                rcvd=websockets.frames.Close(4001, "test"),
                sent=websockets.frames.Close(4001, "test"),
                rcvd_then_sent=False,
            )

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _MockConnect:
        def __call__(self, url, *args, **kwargs):
            return _MockAsyncContextManager(url)

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=lambda event: None,
        api_key="1+FYh/SRE=ds 8Q",
    )

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.websockets.connect",
        _MockConnect(),
    ):
        asyncio.run(client._client_loop())

    assert len(captured_urls) == 1
    assert "session_api_key=1%2BFYh%2FSRE%3Dds%208Q" in captured_urls[0]


def _state_update_payload(event_id: str) -> str:
    return json.dumps(
        {
            "kind": "ConversationStateUpdateEvent",
            "id": event_id,
            "timestamp": "2024-01-01T00:00:00Z",
            "source": "environment",
            "key": FULL_STATE_KEY,
            "value": {"execution_status": "running"},
        }
    )


def _connection_closed(code: int) -> websockets.exceptions.ConnectionClosed:
    return websockets.exceptions.ConnectionClosed(
        rcvd=websockets.frames.Close(code, "test"),
        sent=websockets.frames.Close(code, "test"),
        rcvd_then_sent=False,
    )


class _MockWebSocket:
    def __init__(self, messages, close_code: int):
        self._messages = list(messages)
        self._close_code = close_code

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise _connection_closed(self._close_code)


class _MockWebSocketContext:
    def __init__(self, ws: _MockWebSocket):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_websocket_client_retries_after_retryable_connection_closed():
    """Test that transient WebSocket closures reconnect instead of exiting."""
    connect_calls = 0
    callback_events = []

    def callback(event):
        callback_events.append(event)
        if event.id == "state-2":
            client._stop.set()

    class _MockConnect:
        def __call__(self, url, *args, **kwargs):
            nonlocal connect_calls
            connect_calls += 1
            return _MockWebSocketContext(
                _MockWebSocket(
                    [_state_update_payload(f"state-{connect_calls}")],
                    close_code=1000,
                )
            )

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=callback,
    )

    async def no_sleep(delay):
        return None

    client._sleep_before_retry = no_sleep

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.websockets.connect",
        _MockConnect(),
    ):
        asyncio.run(client._client_loop())

    assert connect_calls == 2
    assert [event.id for event in callback_events] == ["state-1", "state-2"]


@pytest.mark.parametrize("close_code", [4001, 4004])
def test_websocket_client_stops_after_fatal_connection_closed(close_code):
    """Test that fatal WebSocket close codes are not retried."""
    connect_calls = 0

    class _MockAsyncContextManager:
        async def __aenter__(self):
            raise _connection_closed(close_code)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _MockConnect:
        def __call__(self, url, *args, **kwargs):
            nonlocal connect_calls
            connect_calls += 1
            return _MockAsyncContextManager()

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=lambda event: None,
    )

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.websockets.connect",
        _MockConnect(),
    ):
        asyncio.run(client._client_loop())

    assert connect_calls == 1
    assert client._stop.is_set()


def test_websocket_client_calls_on_reconnect_after_subscription_restored():
    """Test reconnect callback runs after the replacement subscription is ready."""
    connect_calls = 0
    reconnect = MagicMock()
    callback_events = []

    def callback(event):
        callback_events.append(event)
        if event.id == "state-2":
            client._stop.set()

    class _MockConnect:
        def __call__(self, url, *args, **kwargs):
            nonlocal connect_calls
            connect_calls += 1
            return _MockWebSocketContext(
                _MockWebSocket(
                    [_state_update_payload(f"state-{connect_calls}")],
                    close_code=1000,
                )
            )

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=callback,
        on_reconnect=reconnect,
    )

    async def no_sleep(delay):
        return None

    client._sleep_before_retry = no_sleep

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.websockets.connect",
        _MockConnect(),
    ):
        asyncio.run(client._client_loop())

    assert connect_calls == 2
    assert [event.id for event in callback_events] == ["state-1", "state-2"]
    reconnect.assert_called_once_with()
