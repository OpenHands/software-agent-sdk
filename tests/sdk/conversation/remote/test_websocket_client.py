"""Tests for WebSocketCallbackClient."""

import asyncio
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import websockets
import websockets.frames

from openhands.sdk.conversation.impl.remote_conversation import WebSocketCallbackClient
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
                rcvd=websockets.frames.Close(1000, "test"),
                sent=websockets.frames.Close(1000, "test"),
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
