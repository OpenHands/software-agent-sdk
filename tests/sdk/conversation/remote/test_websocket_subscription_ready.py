"""Tests for WebSocket subscription readiness signaling.

This module tests the fix for issue #1785 where RemoteEventsList could miss
events emitted between conversation creation and WebSocket subscription completion.

The fix ensures RemoteConversation waits for WebSocket subscription to complete
before allowing operations, using the initial ConversationStateUpdateEvent as
the "ready" signal.
"""

import threading
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from openhands.sdk.conversation.impl.remote_conversation import WebSocketCallbackClient
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, TextContent


@pytest.fixture
def mock_message_event():
    """Create a test MessageEvent."""
    return MessageEvent(
        id="test-message-id",
        timestamp=datetime.now().isoformat(),
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Test message")]
        ),
    )


@pytest.fixture
def mock_state_update_event():
    """Create a test ConversationStateUpdateEvent (the ready signal)."""
    return ConversationStateUpdateEvent(
        id="test-state-update-id",
        timestamp=datetime.now().isoformat(),
        source="environment",
        key=FULL_STATE_KEY,
        value={"execution_status": "idle"},
    )


class TestWebSocketReadySignaling:
    """Tests for WebSocket subscription ready signaling."""

    def test_websocket_client_has_ready_event(self):
        """Test that WebSocketCallbackClient has a _ready threading.Event."""
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        assert hasattr(client, "_ready"), (
            "WebSocketCallbackClient should have _ready attribute"
        )
        assert isinstance(client._ready, threading.Event), (
            "_ready should be a threading.Event"
        )
        assert not client._ready.is_set(), "_ready should not be set initially"

    def test_wait_until_ready_method_exists(self):
        """Test that WebSocketCallbackClient has wait_until_ready method."""
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        assert hasattr(client, "wait_until_ready"), (
            "WebSocketCallbackClient should have wait_until_ready method"
        )
        assert callable(client.wait_until_ready), "wait_until_ready should be callable"

    def test_wait_until_ready_returns_false_on_timeout(self):
        """Test wait_until_ready returns False when timeout expires."""
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Don't send any ready signal - should timeout
        result = client.wait_until_ready(timeout=0.1)

        assert result is False, "wait_until_ready should return False on timeout"

    def test_wait_until_ready_returns_true_when_ready_set(self):
        """Test that wait_until_ready returns True when _ready event is set."""
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Manually set the ready event
        client._ready.set()

        # Should return True immediately
        result = client.wait_until_ready(timeout=0.1)

        assert result is True, "wait_until_ready should return True when ready is set"

    def test_wait_until_ready_blocks_until_ready(self):
        """Test that wait_until_ready blocks until ready signal is received."""
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Set ready after a delay in another thread
        def set_ready_delayed():
            time.sleep(0.2)
            client._ready.set()

        thread = threading.Thread(target=set_ready_delayed)
        thread.start()

        start_time = time.time()
        result = client.wait_until_ready(timeout=2.0)
        elapsed = time.time() - start_time

        thread.join()

        assert result is True, "wait_until_ready should return True"
        assert elapsed >= 0.15, "Should have waited for the ready signal"
        assert elapsed < 1.0, "Should not have waited for full timeout"

    def test_wait_until_ready_is_idempotent(self):
        """Test that wait_until_ready can be called multiple times after ready."""
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Set ready
        client._ready.set()

        # First call should return True immediately
        result1 = client.wait_until_ready(timeout=0.1)
        assert result1 is True

        # Second call should also return True immediately (idempotent)
        result2 = client.wait_until_ready(timeout=0.1)
        assert result2 is True

    def test_wait_until_ready_returns_false_when_stopped(self):
        """Test that wait_until_ready returns False when client is stopped.

        This tests the fix for the issue where wait_until_ready() would block
        for the full timeout even when stop() was called. Now it checks both
        _ready and _stop events, returning False immediately if stopped.
        """
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Set stop event (simulating client being stopped)
        client._stop.set()

        # Should return False immediately, not wait for timeout
        start_time = time.time()
        result = client.wait_until_ready(timeout=5.0)
        elapsed = time.time() - start_time

        assert result is False, "wait_until_ready should return False when stopped"
        assert elapsed < 0.5, (
            f"Should return immediately when stopped, not wait for timeout. "
            f"Elapsed: {elapsed}s"
        )

    def test_wait_until_ready_returns_false_when_stopped_during_wait(self):
        """Test that wait_until_ready returns False when stop is called during wait.

        This tests that if stop() is called while wait_until_ready() is blocking,
        it will return False promptly instead of waiting for the full timeout.
        """
        callback = MagicMock()

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Set stop after a delay in another thread
        def set_stop_delayed():
            time.sleep(0.2)
            client._stop.set()

        thread = threading.Thread(target=set_stop_delayed)
        thread.start()

        start_time = time.time()
        result = client.wait_until_ready(timeout=5.0)
        elapsed = time.time() - start_time

        thread.join()

        assert result is False, "wait_until_ready should return False when stopped"
        assert elapsed >= 0.15, "Should have waited for the stop signal"
        assert elapsed < 1.0, (
            f"Should not have waited for full timeout. Elapsed: {elapsed}s"
        )


class TestWebSocketClientReadyWithEvents:
    """Tests for WebSocket client ready signaling with event processing."""

    def test_ready_not_set_for_non_state_update_events(self, mock_message_event):
        """Test that non-ConversationStateUpdateEvent events don't trigger ready.

        Note: The ready signal is set in _client_loop when processing messages,
        not in the callback. This test verifies the callback doesn't set ready.
        """
        received_events = []

        def callback(event):
            received_events.append(event)

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Calling callback directly doesn't set ready (that happens in _client_loop)
        client.callback(mock_message_event)

        # Callback should be invoked
        assert len(received_events) == 1

        # But ready should not be set (callback doesn't set it)
        assert not client._ready.is_set()

    def test_callback_invoked_regardless_of_ready_state(
        self, mock_message_event, mock_state_update_event
    ):
        """Test that callbacks are always invoked regardless of ready state."""
        received_events = []

        def callback(event):
            received_events.append(event)

        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=callback,
        )

        # Send events
        client.callback(mock_message_event)
        client.callback(mock_state_update_event)
        client.callback(mock_message_event)

        # All callbacks should be invoked
        assert len(received_events) == 3
        assert isinstance(received_events[0], MessageEvent)
        assert isinstance(received_events[1], ConversationStateUpdateEvent)
        assert isinstance(received_events[2], MessageEvent)
