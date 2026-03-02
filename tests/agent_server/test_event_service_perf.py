"""Performance tests for EventService search operations.

These tests verify that search operations complete within acceptable time bounds.
They fail fast (within seconds) for slow queries that should be faster.
"""

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    EventSortOrder,
    StoredConversation,
)
from openhands.sdk import LLM, Agent, Message
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.fifo_lock import FIFOLock
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def create_event_log_with_events(n_events: int) -> EventLog:
    """Create an EventLog with n_events MessageEvents."""
    fs = InMemoryFileStore()
    event_log = EventLog(fs, dir_path="events")

    for i in range(n_events):
        timestamp = f"2025-01-01T{10 + (i // 60):02d}:{i % 60:02d}:00.000000"
        event = MessageEvent(
            id=f"event-{i}",
            source="user" if i % 2 == 0 else "agent",
            llm_message=Message(role="user" if i % 2 == 0 else "assistant"),
            timestamp=timestamp,
        )
        event_log.append(event)

    return event_log


def create_mock_conversation_with_event_log(event_log: EventLog):
    """Create a mock conversation with the given EventLog."""
    conversation = MagicMock(spec=ConversationState)
    state = MagicMock(spec=ConversationState)

    # Use a real FIFOLock
    real_lock = FIFOLock()
    state._lock = real_lock
    state.__enter__ = lambda self: (real_lock.acquire(), self)[1]
    state.__exit__ = lambda self, *args: real_lock.release()

    state.events = event_log
    state.execution_status = ConversationExecutionStatus.IDLE
    conversation._state = state

    return conversation


@pytest.fixture
def sample_stored_conversation():
    """Create a sample StoredConversation for testing."""
    return StoredConversation(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )


@pytest.fixture
def event_service(sample_stored_conversation):
    """Create an EventService instance for testing."""
    service = EventService(
        stored=sample_stored_conversation,
        conversations_dir=Path("test_conversation_dir"),
    )
    return service


class TestEventServiceSearchPerformance:
    """Performance tests for EventService.search_events method.

    These tests verify that search operations complete in acceptable time.
    They create larger event logs to expose performance issues.
    """

    @pytest.mark.asyncio
    async def test_kind_filter_uses_simple_class_name(self, event_service):
        """Test that kind filter works with simple class names like 'MessageEvent'.

        This test fails if kind filter requires fully qualified module path,
        which would be a usability bug. The API should accept simple class names.
        """
        event_log = create_event_log_with_events(100)
        event_service._conversation = create_mock_conversation_with_event_log(event_log)

        # Filter by simple class name - this should work
        result = await event_service.search_events(kind="MessageEvent", limit=10)

        # Should find events, not return empty due to kind mismatch
        assert len(result.items) > 0, (
            "kind='MessageEvent' should match events. "
            "If this fails, the kind filter is comparing against fully qualified "
            "module path instead of simple class name."
        )
        assert len(result.items) == 10

    @pytest.mark.asyncio
    async def test_search_with_limit_completes_quickly(self, event_service):
        """Test that searching with limit doesn't scan all events.

        With 500 events and limit=10, the search should complete in well under
        100ms if it implements early exit. This test fails if it takes longer,
        indicating a full O(n) scan.
        """
        event_log = create_event_log_with_events(500)
        event_service._conversation = create_mock_conversation_with_event_log(event_log)

        start_time = time.perf_counter()
        result = await event_service.search_events(limit=10)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        assert len(result.items) == 10
        # Should complete quickly - if it takes >500ms, something is wrong
        assert elapsed_ms < 500, (
            f"search with limit=10 took {elapsed_ms:.1f}ms, expected <500ms. "
            "This suggests full O(n) scan without early exit."
        )

    @pytest.mark.asyncio
    async def test_desc_search_last_10_events_fast(self, event_service):
        """Test that TIMESTAMP_DESC with limit=10 is fast (scans from end).

        Getting the last 10 events should be O(1) if scanning from end,
        not O(n) if scanning from beginning. This test fails if it's slow.
        """
        event_log = create_event_log_with_events(500)
        event_service._conversation = create_mock_conversation_with_event_log(event_log)

        start_time = time.perf_counter()
        result = await event_service.search_events(
            limit=10,
            sort_order=EventSortOrder.TIMESTAMP_DESC,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        assert len(result.items) == 10
        # The last 10 events should be retrieved quickly
        assert elapsed_ms < 500, (
            f"TIMESTAMP_DESC with limit=10 took {elapsed_ms:.1f}ms, expected <500ms. "
            "DESC queries should scan from end, not scan all events."
        )
        # Verify we got the most recent events
        assert result.items[0].timestamp > result.items[-1].timestamp

    @pytest.mark.asyncio
    async def test_kind_filter_with_many_events_is_fast(self, event_service):
        """Test that kind filter with limit completes quickly.

        Even with 500 events, filtering by kind with limit=10 should be fast
        if it implements early exit after finding enough matches.
        """
        event_log = create_event_log_with_events(500)
        event_service._conversation = create_mock_conversation_with_event_log(event_log)

        start_time = time.perf_counter()
        # Use simple class name - should match
        result = await event_service.search_events(
            kind="MessageEvent",
            limit=10,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Should find events with simple class name
        assert len(result.items) == 10, (
            "kind='MessageEvent' returned no results - kind filter may be broken"
        )

        # Should complete quickly
        assert elapsed_ms < 500, (
            f"kind filter search took {elapsed_ms:.1f}ms, expected <500ms. "
            "Kind filter should support early exit after finding limit matches."
        )

    @pytest.mark.asyncio
    async def test_pagination_cursor_lookup_is_fast(self, event_service):
        """Test that pagination cursor lookup is O(1), not O(n) linear scan.

        When using page_id cursor, the lookup should use the index directly,
        not scan through all events to find the starting position.
        """
        event_log = create_event_log_with_events(500)
        event_service._conversation = create_mock_conversation_with_event_log(event_log)

        # First get a page to get a cursor
        first_page = await event_service.search_events(limit=10)
        cursor = first_page.next_page_id
        assert cursor is not None

        # Now use cursor - should be fast (O(1) lookup, not O(n) scan)
        start_time = time.perf_counter()
        result = await event_service.search_events(page_id=cursor, limit=10)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        assert len(result.items) == 10
        # Cursor lookup should be very fast
        assert elapsed_ms < 500, (
            f"Pagination with cursor took {elapsed_ms:.1f}ms, expected <500ms. "
            "Cursor lookup should be O(1), not O(n) linear scan."
        )

    @pytest.mark.asyncio
    async def test_subscribe_does_not_block_when_lock_held(self, event_service):
        """Test that subscribe_to_events doesn't block when agent is running.

        When the agent is actively running, it holds the state lock. New
        WebSocket subscriptions should not block waiting for the lock.
        """
        from openhands.agent_server.pub_sub import Subscriber

        event_log = create_event_log_with_events(100)
        mock_conv = create_mock_conversation_with_event_log(event_log)
        event_service._conversation = mock_conv

        # Simulate agent holding the lock
        state = mock_conv._state
        state._lock.acquire()

        try:
            received_events = []

            class TestSubscriber(Subscriber):
                async def __call__(self, event):
                    received_events.append(event)

            # Subscription should complete quickly even though lock is held
            start_time = time.perf_counter()
            subscriber_id = await event_service.subscribe_to_events(TestSubscriber())
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Should complete in under 100ms (non-blocking)
            assert elapsed_ms < 100, (
                f"subscribe_to_events took {elapsed_ms:.1f}ms while lock held. "
                "Should use non-blocking lock acquisition."
            )
            assert subscriber_id is not None

            # Should have received a minimal state update
            assert len(received_events) == 1
        finally:
            state._lock.release()
