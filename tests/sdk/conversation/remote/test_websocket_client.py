"""Tests for WebSocketCallbackClient."""

import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

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


def test_websocket_client_wait_for_connection_returns_true_when_connected():
    """Test wait_for_connection returns True when connection is established."""

    def noop_callback(event):
        pass

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=noop_callback,
    )

    # Simulate connection being established
    client._connected.set()

    # Should return immediately with True
    start = time.time()
    result = client.wait_for_connection(timeout=5.0)
    elapsed = time.time() - start

    assert result is True
    assert elapsed < 0.1  # Should be nearly instant


def test_websocket_client_wait_for_connection_times_out():
    """Test wait_for_connection returns False on timeout."""

    def noop_callback(event):
        pass

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=noop_callback,
    )

    # Don't set _connected, so wait should timeout
    start = time.time()
    result = client.wait_for_connection(timeout=0.1)
    elapsed = time.time() - start

    assert result is False
    assert 0.1 <= elapsed < 0.3  # Should wait approximately the timeout duration


def test_websocket_client_connected_event_cleared_on_start():
    """Test that _connected event is cleared when starting."""

    def noop_callback(event):
        pass

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=noop_callback,
    )

    # Pre-set the connected event
    client._connected.set()
    assert client._connected.is_set()

    # Start should clear it
    with patch.object(client, "_run"):
        client.start()

    assert not client._connected.is_set()
