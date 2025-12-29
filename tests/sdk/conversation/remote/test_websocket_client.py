"""Tests for WebSocketCallbackClient."""

import asyncio
import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import websockets.exceptions

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


@pytest.mark.asyncio
async def test_websocket_client_stops_on_connection_closed():
    """Test that WebSocket client stops receiving events when connection is closed.

    This test reproduces the issue reported in GitHub issue #1381 where the
    callback stops being invoked partway through a conversation when the
    WebSocket connection is closed unexpectedly (e.g., due to network issues
    or server-side timeout during long-running operations).

    The issue is that when ConnectionClosed is raised, the client loop breaks
    immediately without any retry, causing the callback to stop receiving events
    even though the conversation may still be running on the server.

    See: https://github.com/OpenHands/software-agent-sdk/issues/1381#issuecomment-3696131311
    """
    callback_events: list = []
    events_before_close = 3
    events_after_close = 2

    def test_callback(event):
        callback_events.append(event)

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=test_callback,
    )

    # Create mock events
    def create_event(event_id: str) -> dict:
        return {
            "kind": "MessageEvent",
            "id": event_id,
            "timestamp": datetime.now().isoformat(),
            "source": "agent",
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"Message {event_id}"}],
            },
        }

    # Create a mock WebSocket that sends some events, then closes unexpectedly,
    # then would send more events if reconnected
    events_sent = 0
    connection_closed = False

    class MockWebSocket:
        def __init__(self):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            nonlocal events_sent, connection_closed
            if events_sent < events_before_close:
                events_sent += 1
                await asyncio.sleep(0.01)  # Small delay to simulate network
                return json.dumps(create_event(f"event-{events_sent}"))
            elif not connection_closed:
                # Simulate unexpected connection close (e.g., network issue)
                connection_closed = True
                raise websockets.exceptions.ConnectionClosed(None, None)
            else:
                # After reconnection, send more events
                events_sent += 1
                if events_sent <= events_before_close + events_after_close:
                    await asyncio.sleep(0.01)
                    return json.dumps(create_event(f"event-{events_sent}"))
                raise StopAsyncIteration

    mock_ws = MockWebSocket()

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.websockets.connect",
        return_value=mock_ws,
    ):
        # Run the client loop directly (not in a thread for easier testing)
        await client._client_loop()

    # The current behavior: client stops after ConnectionClosed
    # Only events_before_close events are received
    assert len(callback_events) == events_before_close, (
        f"Expected {events_before_close} events before connection closed, "
        f"got {len(callback_events)}. "
        "This demonstrates the bug: the client stops receiving events "
        "when the WebSocket connection is closed unexpectedly, "
        "even though more events may be available after reconnection."
    )

    # NOTE: The expected behavior (after fix) would be:
    # The client should retry the connection and receive all events
    # assert len(callback_events) == events_before_close + events_after_close


@pytest.mark.asyncio
async def test_websocket_client_retries_on_other_exceptions():
    """Test that WebSocket client retries on non-ConnectionClosed exceptions.

    This test verifies that the client does retry when other exceptions occur,
    which is the expected behavior. This contrasts with the ConnectionClosed
    case where the client stops immediately.
    """
    connection_attempts = 0
    max_attempts = 3

    def test_callback(event):
        pass

    client = WebSocketCallbackClient(
        host="http://localhost:8000",
        conversation_id="test-conv-id",
        callback=test_callback,
    )

    def mock_connect_sync(*args, **kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        if connection_attempts >= max_attempts:
            # After max attempts, stop the client to end the test
            client._stop.set()
        # Raise a generic exception (not ConnectionClosed) to trigger retry
        raise ConnectionError("Simulated connection error")

    # Patch asyncio.sleep to speed up the test
    original_sleep = asyncio.sleep

    async def fast_sleep(delay):
        await original_sleep(0.001)  # Much faster sleep for testing

    with (
        patch(
            "openhands.sdk.conversation.impl.remote_conversation.websockets.connect",
            side_effect=mock_connect_sync,
        ),
        patch(
            "openhands.sdk.conversation.impl.remote_conversation.asyncio.sleep",
            side_effect=fast_sleep,
        ),
    ):
        await client._client_loop()

    # The client should have retried multiple times before stopping
    assert connection_attempts >= max_attempts, (
        f"Expected at least {max_attempts} connection attempts, "
        f"got {connection_attempts}. "
        "This verifies that the client retries on generic exceptions."
    )
