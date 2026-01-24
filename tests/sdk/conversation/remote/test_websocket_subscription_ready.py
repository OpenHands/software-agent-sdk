"""Tests for RemoteEventsList reconciliation + WebSocket readiness wait.

We keep these tests focused on behavior and avoid "tests that test that code exists"
(e.g., hasattr/callable checks).

High-value behavior:
- WebSocketCallbackClient.wait_until_ready() obeys timeout and unblocks on signals.
- RemoteEventsList.reconcile() deduplicates events by id and is idempotent.
"""

import threading
from unittest.mock import MagicMock, patch

from openhands.sdk.conversation.impl.remote_conversation import (
    RemoteEventsList,
    WebSocketCallbackClient,
)
from openhands.sdk.event.conversation_state import FULL_STATE_KEY


class TestWebSocketReadySignaling:
    def test_wait_until_ready_returns_false_on_timeout(self):
        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=MagicMock(),
        )

        assert client.wait_until_ready(timeout=0.05) is False

    def test_wait_until_ready_unblocks_when_ready_signaled(self):
        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=MagicMock(),
        )

        result: dict[str, bool | None] = {"value": None}

        def wait_for_ready() -> None:
            result["value"] = client.wait_until_ready(timeout=1.0)

        waiter = threading.Thread(target=wait_for_ready)
        waiter.start()

        # Ensure it doesn't return immediately (i.e. it actually blocks).
        waiter.join(timeout=0.1)
        assert waiter.is_alive()

        client._ready.set()
        waiter.join(timeout=1.0)

        assert not waiter.is_alive()
        assert result["value"] is True

    def test_wait_until_ready_unblocks_when_stopped(self):
        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=MagicMock(),
        )

        result: dict[str, bool | None] = {"value": None}

        def wait_for_ready() -> None:
            result["value"] = client.wait_until_ready(timeout=1.0)

        waiter = threading.Thread(target=wait_for_ready)
        waiter.start()

        waiter.join(timeout=0.02)
        assert waiter.is_alive()

        client._stop.set()
        waiter.join(timeout=1.0)

        assert not waiter.is_alive()
        assert result["value"] is False

    def test_wait_until_ready_is_idempotent_after_ready(self):
        client = WebSocketCallbackClient(
            host="http://localhost:8000",
            conversation_id="test-conv-id",
            callback=MagicMock(),
        )

        client._ready.set()

        assert client.wait_until_ready(timeout=0.1) is True
        assert client.wait_until_ready(timeout=0.1) is True


class TestRemoteEventsListReconciliation:
    def test_reconcile_merges_events_without_duplicates(self):
        mock_client = MagicMock()

        def make_state_event(event_id: str, timestamp: str) -> dict:
            return {
                "kind": "ConversationStateUpdateEvent",
                "id": event_id,
                "timestamp": timestamp,
                "source": "environment",
                "key": FULL_STATE_KEY,
                "value": {"execution_status": "idle"},
            }

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation._send_request"
        ) as mock_send:
            mock_response = MagicMock()
            mock_response.json.side_effect = [
                {
                    "items": [make_state_event("event-1", "2024-01-01T00:00:01Z")],
                    "next_page_id": None,
                },
                {
                    "items": [
                        make_state_event("event-1", "2024-01-01T00:00:01Z"),
                        make_state_event("event-2", "2024-01-01T00:00:02Z"),
                        make_state_event("event-3", "2024-01-01T00:00:03Z"),
                    ],
                    "next_page_id": None,
                },
            ]
            mock_send.return_value = mock_response

            events_list = RemoteEventsList(mock_client, "test-conv-id")
            assert [e.id for e in events_list] == ["event-1"]

            added_count = events_list.reconcile()
            assert added_count == 2
            assert [e.id for e in events_list] == ["event-1", "event-2", "event-3"]
            assert len({e.id for e in events_list}) == len(events_list)

    def test_reconcile_handles_empty_server_response(self):
        mock_client = MagicMock()

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation._send_request"
        ) as mock_send:
            mock_response = MagicMock()
            mock_response.json.side_effect = [
                {"items": [], "next_page_id": None},
                {"items": [], "next_page_id": None},
            ]
            mock_send.return_value = mock_response

            events_list = RemoteEventsList(mock_client, "test-conv-id")
            assert list(events_list) == []

            assert events_list.reconcile() == 0
            assert list(events_list) == []

    def test_reconcile_is_idempotent(self):
        mock_client = MagicMock()

        def make_state_event(event_id: str, timestamp: str) -> dict:
            return {
                "kind": "ConversationStateUpdateEvent",
                "id": event_id,
                "timestamp": timestamp,
                "source": "environment",
                "key": FULL_STATE_KEY,
                "value": {"execution_status": "idle"},
            }

        def make_response():
            return {
                "items": [
                    make_state_event("event-1", "2024-01-01T00:00:01Z"),
                    make_state_event("event-2", "2024-01-01T00:00:02Z"),
                ],
                "next_page_id": None,
            }

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation._send_request"
        ) as mock_send:
            mock_response = MagicMock()
            mock_response.json.side_effect = lambda: make_response()
            mock_send.return_value = mock_response

            events_list = RemoteEventsList(mock_client, "test-conv-id")
            assert [e.id for e in events_list] == ["event-1", "event-2"]

            assert events_list.reconcile() == 0
            assert [e.id for e in events_list] == ["event-1", "event-2"]

            assert events_list.reconcile() == 0
            assert [e.id for e in events_list] == ["event-1", "event-2"]
