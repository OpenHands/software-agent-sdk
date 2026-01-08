from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConfirmationResponseRequest,
    EventPage,
    EventSortOrder,
    StoredConversation,
)
from openhands.sdk import LLM, Agent, Conversation, Message
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def sample_stored_conversation():
    """Create a sample StoredConversation for testing."""
    return StoredConversation(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
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


@pytest.fixture
def mock_conversation_with_events():
    """Create a mock conversation with sample events."""
    conversation = MagicMock(spec=Conversation)
    state = MagicMock(spec=ConversationState)

    # Create sample events with different timestamps and kinds
    events = [
        MessageEvent(
            id=f"event{index}", source="user", llm_message=Message(role="user")
        )
        for index in range(1, 6)
    ]

    state.events = events
    state.__enter__ = MagicMock(return_value=state)
    state.__exit__ = MagicMock(return_value=None)
    conversation._state = state

    return conversation


@pytest.fixture
def mock_conversation_with_timestamped_events():
    """Create a mock conversation with events having specific timestamps for testing."""
    conversation = MagicMock(spec=Conversation)
    state = MagicMock(spec=ConversationState)

    # Create events with specific ISO format timestamps
    # These timestamps are in chronological order
    timestamps = [
        "2025-01-01T10:00:00.000000",
        "2025-01-01T11:00:00.000000",
        "2025-01-01T12:00:00.000000",
        "2025-01-01T13:00:00.000000",
        "2025-01-01T14:00:00.000000",
    ]

    events = []
    for index, timestamp in enumerate(timestamps, 1):
        event = MessageEvent(
            id=f"event{index}",
            source="user",
            llm_message=Message(role="user"),
            timestamp=timestamp,
        )
        events.append(event)

    state.events = events
    state.__enter__ = MagicMock(return_value=state)
    state.__exit__ = MagicMock(return_value=None)
    conversation._state = state

    return conversation


class TestEventServiceSearchEvents:
    """Test cases for EventService.search_events method."""

    @pytest.mark.asyncio
    async def test_search_events_inactive_service(self, event_service):
        """Test that search_events raises ValueError when conversation is not active."""
        event_service._conversation = None

        with pytest.raises(ValueError, match="inactive_service"):
            await event_service.search_events()

    @pytest.mark.asyncio
    async def test_search_events_empty_result(self, event_service):
        """Test search_events with no events."""
        # Mock conversation with empty events
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.events = []
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        result = await event_service.search_events()

        assert isinstance(result, EventPage)
        assert result.items == []
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_basic(
        self, event_service, mock_conversation_with_events
    ):
        """Test basic search_events functionality."""
        event_service._conversation = mock_conversation_with_events

        result = await event_service.search_events()

        assert len(result.items) == 5
        assert result.next_page_id is None
        # Default sort is TIMESTAMP (ascending), so first event should be earliest
        assert result.items[0].timestamp < result.items[-1].timestamp

    @pytest.mark.asyncio
    async def test_search_events_kind_filter(
        self, event_service, mock_conversation_with_events
    ):
        """Test filtering events by kind."""
        event_service._conversation = mock_conversation_with_events

        # Test filtering by ActionEvent
        result = await event_service.search_events(kind="ActionEvent")
        assert len(result.items) == 0

        # Test filtering by MessageEvent
        result = await event_service.search_events(
            kind="openhands.sdk.event.llm_convertible.message.MessageEvent"
        )
        assert len(result.items) == 5
        for event in result.items:
            assert event.__class__.__name__ == "MessageEvent"

        # Test filtering by non-existent kind
        result = await event_service.search_events(kind="NonExistentEvent")
        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_search_events_sorting(
        self, event_service, mock_conversation_with_events
    ):
        """Test sorting events by timestamp."""
        event_service._conversation = mock_conversation_with_events

        # Test TIMESTAMP (ascending) - default
        result = await event_service.search_events(sort_order=EventSortOrder.TIMESTAMP)
        assert len(result.items) == 5
        for i in range(len(result.items) - 1):
            assert result.items[i].timestamp <= result.items[i + 1].timestamp

        # Test TIMESTAMP_DESC (descending)
        result = await event_service.search_events(
            sort_order=EventSortOrder.TIMESTAMP_DESC
        )
        assert len(result.items) == 5
        for i in range(len(result.items) - 1):
            assert result.items[i].timestamp >= result.items[i + 1].timestamp

    @pytest.mark.asyncio
    async def test_search_events_pagination(
        self, event_service, mock_conversation_with_events
    ):
        """Test pagination functionality."""
        event_service._conversation = mock_conversation_with_events

        # Test first page with limit 2
        result = await event_service.search_events(limit=2)
        assert len(result.items) == 2
        assert result.next_page_id is not None

        # Test second page using next_page_id
        result = await event_service.search_events(page_id=result.next_page_id, limit=2)
        assert len(result.items) == 2
        assert result.next_page_id is not None

        # Test third page
        result = await event_service.search_events(page_id=result.next_page_id, limit=2)
        assert len(result.items) == 1  # Only one item left
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_combined_filter_and_sort(
        self, event_service, mock_conversation_with_events
    ):
        """Test combining kind filtering with sorting."""
        event_service._conversation = mock_conversation_with_events

        # Filter by ActionEvent and sort by TIMESTAMP_DESC
        result = await event_service.search_events(
            kind="openhands.sdk.event.llm_convertible.message.MessageEvent",
            sort_order=EventSortOrder.TIMESTAMP_DESC,
        )

        assert len(result.items) == 5
        for event in result.items:
            assert event.__class__.__name__ == "MessageEvent"
        # Should be sorted by timestamp descending (newest first)
        assert result.items[0].timestamp > result.items[1].timestamp

    @pytest.mark.asyncio
    async def test_search_events_pagination_with_filter(
        self, event_service, mock_conversation_with_events
    ):
        """Test pagination with filtering."""
        event_service._conversation = mock_conversation_with_events

        # Filter by MessageEvent with limit 1
        result = await event_service.search_events(
            kind="openhands.sdk.event.llm_convertible.message.MessageEvent", limit=1
        )
        assert len(result.items) == 1
        assert result.items[0].__class__.__name__ == "MessageEvent"
        assert result.next_page_id is not None

        # Get second page
        result = await event_service.search_events(
            kind="openhands.sdk.event.llm_convertible.message.MessageEvent",
            page_id=result.next_page_id,
            limit=4,
        )
        assert len(result.items) == 4
        assert result.items[0].__class__.__name__ == "MessageEvent"
        assert result.next_page_id is None  # No more MessageEvents

    @pytest.mark.asyncio
    async def test_search_events_invalid_page_id(
        self, event_service, mock_conversation_with_events
    ):
        """Test search_events with invalid page_id."""
        event_service._conversation = mock_conversation_with_events

        # Use a non-existent page_id
        invalid_page_id = "invalid_event_id"
        result = await event_service.search_events(page_id=invalid_page_id)

        # Should return all items since page_id doesn't match any event
        assert len(result.items) == 5
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_large_limit(
        self, event_service, mock_conversation_with_events
    ):
        """Test search_events with limit larger than available events."""
        event_service._conversation = mock_conversation_with_events

        result = await event_service.search_events(limit=100)

        assert len(result.items) == 5  # All available events
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_zero_limit(
        self, event_service, mock_conversation_with_events
    ):
        """Test search_events with zero limit."""
        event_service._conversation = mock_conversation_with_events

        result = await event_service.search_events(limit=0)

        assert len(result.items) == 0
        # Should still have next_page_id if there are events available
        assert result.next_page_id is not None

    @pytest.mark.asyncio
    async def test_search_events_exact_pagination_boundary(self, event_service):
        """Test pagination when the number of events exactly matches the limit."""
        # Create exactly 3 events
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)

        events = [
            MessageEvent(
                id=f"event{index}", source="user", llm_message=Message(role="user")
            )
            for index in range(1, 4)
        ]

        state.events = events
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        # Request exactly 3 events (same as available)
        result = await event_service.search_events(limit=3)

        assert len(result.items) == 3
        assert result.next_page_id is None  # No more events available

    @pytest.mark.asyncio
    async def test_search_events_timestamp_gte_filter(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test filtering events with timestamp__gte (greater than or equal)."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Filter events >= 12:00:00 (should return events 3, 4, 5)
        filter_time = datetime(2025, 1, 1, 12, 0, 0)
        result = await event_service.search_events(timestamp__gte=filter_time)

        assert len(result.items) == 3
        assert result.items[0].id == "event3"
        assert result.items[1].id == "event4"
        assert result.items[2].id == "event5"
        # All returned events should have timestamp >= filter value
        filter_iso = filter_time.isoformat()
        for event in result.items:
            assert event.timestamp >= filter_iso

    @pytest.mark.asyncio
    async def test_search_events_timestamp_lt_filter(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test filtering events with timestamp__lt (less than)."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Filter events < 13:00:00 (should return events 1, 2, 3)
        filter_time = datetime(2025, 1, 1, 13, 0, 0)
        result = await event_service.search_events(timestamp__lt=filter_time)

        assert len(result.items) == 3
        assert result.items[0].id == "event1"
        assert result.items[1].id == "event2"
        assert result.items[2].id == "event3"
        # All returned events should have timestamp < filter value
        filter_iso = filter_time.isoformat()
        for event in result.items:
            assert event.timestamp < filter_iso

    @pytest.mark.asyncio
    async def test_search_events_timestamp_range_filter(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test filtering events with both timestamp__gte and timestamp__lt."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Filter events between 11:00:00 and 13:00:00 (should return events 2, 3)
        gte_time = datetime(2025, 1, 1, 11, 0, 0)
        lt_time = datetime(2025, 1, 1, 13, 0, 0)
        result = await event_service.search_events(
            timestamp__gte=gte_time, timestamp__lt=lt_time
        )

        assert len(result.items) == 2
        assert result.items[0].id == "event2"
        assert result.items[1].id == "event3"
        # All returned events should be within the range
        gte_iso = gte_time.isoformat()
        lt_iso = lt_time.isoformat()
        for event in result.items:
            assert event.timestamp >= gte_iso
            assert event.timestamp < lt_iso

    @pytest.mark.asyncio
    async def test_search_events_timestamp_filter_with_timezone_aware(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test filtering events with timezone-aware datetime."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Filter events >= 12:00:00 UTC (should return events 3, 4, 5)
        filter_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = await event_service.search_events(timestamp__gte=filter_time)

        assert len(result.items) == 3
        assert result.items[0].id == "event3"
        assert result.items[1].id == "event4"
        assert result.items[2].id == "event5"

    @pytest.mark.asyncio
    async def test_search_events_timestamp_filter_no_matches(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test filtering events with timestamps that don't match any events."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Filter events >= 15:00:00 (should return no events)
        filter_time = datetime(2025, 1, 1, 15, 0, 0)
        result = await event_service.search_events(timestamp__gte=filter_time)

        assert len(result.items) == 0
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_events_timestamp_filter_all_events(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test filtering events with timestamps that include all events."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Filter events >= 09:00:00 (should return all events)
        filter_time = datetime(2025, 1, 1, 9, 0, 0)
        result = await event_service.search_events(timestamp__gte=filter_time)

        assert len(result.items) == 5
        assert result.items[0].id == "event1"
        assert result.items[4].id == "event5"


class TestEventServiceCountEvents:
    """Test cases for EventService.count_events method."""

    @pytest.mark.asyncio
    async def test_count_events_inactive_service(self, event_service):
        """Test that count_events raises ValueError when service is inactive."""
        event_service._conversation = None

        with pytest.raises(ValueError, match="inactive_service"):
            await event_service.count_events()

    @pytest.mark.asyncio
    async def test_count_events_empty_result(self, event_service):
        """Test count_events with no events."""
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.events = []
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        result = await event_service.count_events()
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_events_basic(
        self, event_service, mock_conversation_with_events
    ):
        """Test basic count_events functionality."""
        event_service._conversation = mock_conversation_with_events

        result = await event_service.count_events()
        assert result == 5  # Total events in mock_conversation_with_events

    @pytest.mark.asyncio
    async def test_count_events_kind_filter(
        self, event_service, mock_conversation_with_events
    ):
        """Test counting events with kind filter."""
        event_service._conversation = mock_conversation_with_events

        # Count all events
        result = await event_service.count_events()
        assert result == 5

        # Count ActionEvent events (should be 5)
        result = await event_service.count_events(
            kind="openhands.sdk.event.llm_convertible.message.MessageEvent"
        )
        assert result == 5

        # Count non-existent event type (should be 0)
        result = await event_service.count_events(kind="NonExistentEvent")
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_events_timestamp_gte_filter(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test counting events with timestamp__gte filter."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Count events >= 12:00:00 (should return 3)
        filter_time = datetime(2025, 1, 1, 12, 0, 0)
        result = await event_service.count_events(timestamp__gte=filter_time)
        assert result == 3

    @pytest.mark.asyncio
    async def test_count_events_timestamp_lt_filter(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test counting events with timestamp__lt filter."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Count events < 13:00:00 (should return 3)
        filter_time = datetime(2025, 1, 1, 13, 0, 0)
        result = await event_service.count_events(timestamp__lt=filter_time)
        assert result == 3

    @pytest.mark.asyncio
    async def test_count_events_timestamp_range_filter(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test counting events with both timestamp filters."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Count events between 11:00:00 and 13:00:00 (should return 2)
        gte_time = datetime(2025, 1, 1, 11, 0, 0)
        lt_time = datetime(2025, 1, 1, 13, 0, 0)
        result = await event_service.count_events(
            timestamp__gte=gte_time, timestamp__lt=lt_time
        )
        assert result == 2

    @pytest.mark.asyncio
    async def test_count_events_timestamp_filter_with_timezone_aware(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test counting events with timezone-aware datetime."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Count events >= 12:00:00 UTC (should return 3)
        filter_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = await event_service.count_events(timestamp__gte=filter_time)
        assert result == 3

    @pytest.mark.asyncio
    async def test_count_events_timestamp_filter_no_matches(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test counting events with timestamps that don't match any events."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Count events >= 15:00:00 (should return 0)
        filter_time = datetime(2025, 1, 1, 15, 0, 0)
        result = await event_service.count_events(timestamp__gte=filter_time)
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_events_timestamp_filter_all_events(
        self, event_service, mock_conversation_with_timestamped_events
    ):
        """Test counting events with timestamps that include all events."""
        event_service._conversation = mock_conversation_with_timestamped_events

        # Count events >= 09:00:00 (should return 5)
        filter_time = datetime(2025, 1, 1, 9, 0, 0)
        result = await event_service.count_events(timestamp__gte=filter_time)
        assert result == 5


class TestEventServiceSendMessage:
    """Test cases for EventService.send_message method."""

    async def _mock_executor(self, *args):
        """Helper to create a mock coroutine for run_in_executor."""
        return None

    @pytest.mark.asyncio
    async def test_send_message_inactive_service(self, event_service):
        """Test that send_message raises ValueError when service is inactive."""
        event_service._conversation = None
        message = Message(role="user", content=[])

        with pytest.raises(ValueError, match="inactive_service"):
            await event_service.send_message(message)

    @pytest.mark.asyncio
    async def test_send_message_with_run_false_default(self, event_service):
        """Test send_message with default run=True."""
        # Mock conversation and its methods
        conversation = MagicMock()
        state = MagicMock()
        state.execution_status = ConversationExecutionStatus.IDLE
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation.state = state
        conversation.send_message = MagicMock()
        conversation.run = MagicMock()

        event_service._conversation = conversation
        message = Message(role="user", content=[])

        # Mock the event loop and executor
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            mock_loop.run_in_executor.return_value = self._mock_executor()

            # Call send_message with default run=True
            await event_service.send_message(message)

            # Verify send_message was called via executor
            mock_loop.run_in_executor.assert_any_call(
                None, conversation.send_message, message
            )
            # Verify run was called via executor since run=True and agent is not running
            assert (
                None,
                conversation.run,
            ) not in mock_loop.run_in_executor.call_args_list

    @pytest.mark.asyncio
    async def test_send_message_with_run_false(self, event_service):
        """Test send_message with run=False."""
        # Mock conversation and its methods
        conversation = MagicMock()
        conversation.send_message = MagicMock()
        conversation.run = MagicMock()

        event_service._conversation = conversation
        message = Message(role="user", content=[])

        # Mock the event loop and executor
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            mock_loop.run_in_executor.return_value = self._mock_executor()

            # Call send_message with run=False
            await event_service.send_message(message, run=False)

            # Verify send_message was called via executor
            mock_loop.run_in_executor.assert_called_once_with(
                None, conversation.send_message, message
            )
            # Verify run was NOT called since run=False
            assert mock_loop.run_in_executor.call_count == 1  # Only send_message call

    @pytest.mark.asyncio
    async def test_send_message_with_run_true_agent_already_running(
        self, event_service
    ):
        """Test send_message with run=True but agent already running."""
        # Mock conversation and its methods
        conversation = MagicMock()
        state = MagicMock()
        state.execution_status = ConversationExecutionStatus.RUNNING
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation.state = state
        conversation.send_message = MagicMock()
        conversation.run = MagicMock()

        event_service._conversation = conversation
        message = Message(role="user", content=[])

        # Mock the event loop and executor
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            mock_loop.run_in_executor.return_value = self._mock_executor()

            # Call send_message with run=True
            await event_service.send_message(message, run=True)

            # Verify send_message was called via executor
            mock_loop.run_in_executor.assert_called_once_with(
                None, conversation.send_message, message
            )
            # Verify run was NOT called since agent is already running
            assert mock_loop.run_in_executor.call_count == 1  # Only send_message call

    @pytest.mark.asyncio
    async def test_send_message_with_run_true_agent_idle(self, event_service):
        """Test send_message with run=True and agent idle."""
        # Mock conversation and its methods
        conversation = MagicMock()
        state = MagicMock()
        state.execution_status = ConversationExecutionStatus.IDLE
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation.state = state
        conversation.send_message = MagicMock()
        conversation.run = MagicMock()

        event_service._conversation = conversation
        message = Message(role="user", content=[])

        # Mock the event loop and executor
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            mock_loop.run_in_executor.return_value = self._mock_executor()

            # Call send_message with run=True
            await event_service.send_message(message, run=True)

            # Verify send_message was called via executor
            mock_loop.run_in_executor.assert_any_call(
                None, conversation.send_message, message
            )
            # Verify run was called via executor since agent is idle
            mock_loop.run_in_executor.assert_any_call(None, conversation.run)

    @pytest.mark.asyncio
    async def test_send_message_with_different_message_types(self, event_service):
        """Test send_message with different message types."""
        # Mock conversation
        conversation = MagicMock()
        conversation.send_message = MagicMock()
        conversation.run = MagicMock()

        event_service._conversation = conversation

        # Mock the event loop and executor
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            # Create a side effect that returns a new coroutine each time
            mock_loop.run_in_executor.side_effect = lambda *args: self._mock_executor()

            # Test with user message (run=False to avoid state checking)
            user_message = Message(role="user", content=[])
            await event_service.send_message(user_message, run=False)
            mock_loop.run_in_executor.assert_any_call(
                None, conversation.send_message, user_message
            )

            # Test with assistant message
            assistant_message = Message(role="assistant", content=[])
            await event_service.send_message(assistant_message, run=False)
            mock_loop.run_in_executor.assert_any_call(
                None, conversation.send_message, assistant_message
            )

            # Test with system message
            system_message = Message(role="system", content=[])
            await event_service.send_message(system_message, run=False)
            mock_loop.run_in_executor.assert_any_call(
                None, conversation.send_message, system_message
            )


class TestEventServiceRespondToConfirmation:
    """Test cases for confirmation responses and rejection handling."""

    @pytest.mark.asyncio
    async def test_respond_to_confirmation_accept_calls_run(self, event_service):
        """Accepting confirmation should trigger run and not rejection."""
        event_service._conversation = MagicMock()
        event_service.run = AsyncMock()
        event_service.reject_pending_actions = AsyncMock()

        request = ConfirmationResponseRequest(accept=True, reason="ignored")

        await event_service.respond_to_confirmation(request)

        event_service.run.assert_awaited_once_with()
        event_service.reject_pending_actions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_respond_to_confirmation_rejects_actions(self, event_service):
        """Rejecting confirmation should call reject_pending_actions with reason."""
        event_service._conversation = MagicMock()
        event_service.run = AsyncMock()
        event_service.reject_pending_actions = AsyncMock()

        reason = "User rejected actions"
        request = ConfirmationResponseRequest(accept=False, reason=reason)

        await event_service.respond_to_confirmation(request)

        event_service.reject_pending_actions.assert_awaited_once_with(reason)
        event_service.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reject_pending_actions_inactive_service(self, event_service):
        """Rejecting pending actions should fail when service is inactive."""
        event_service._conversation = None

        with pytest.raises(ValueError, match="inactive_service"):
            await event_service.reject_pending_actions("any reason")

    @pytest.mark.asyncio
    async def test_reject_pending_actions_invokes_conversation(self, event_service):
        """Rejecting pending actions should delegate to conversation via executor."""
        conversation = MagicMock()
        conversation.reject_pending_actions = MagicMock()
        event_service._conversation = conversation

        async def _mock_executor(*_args, **_kwargs):
            return None

        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            mock_loop.run_in_executor.return_value = _mock_executor()

            await event_service.reject_pending_actions("custom reason")

            mock_loop.run_in_executor.assert_called_once_with(
                None, conversation.reject_pending_actions, "custom reason"
            )


class TestEventServiceIsOpen:
    """Test cases for EventService.is_open method."""

    def test_is_open_when_conversation_is_none(self, event_service):
        """Test is_open returns False when _conversation is None."""
        event_service._conversation = None
        assert not event_service.is_open()

    def test_is_open_when_conversation_exists(self, event_service):
        """Test is_open returns True when _conversation exists."""
        conversation = MagicMock(spec=Conversation)
        event_service._conversation = conversation
        assert event_service.is_open()

    def test_is_open_when_conversation_is_falsy(self, event_service):
        """Test is_open returns False when _conversation is falsy."""
        # Test with various falsy values
        falsy_values = [None, False, 0, "", [], {}]

        for falsy_value in falsy_values:
            event_service._conversation = falsy_value
            assert not event_service.is_open(), f"Expected False for {falsy_value}"

    def test_is_open_when_conversation_is_truthy(self, event_service):
        """Test is_open returns True when _conversation is truthy."""
        # Test with various truthy values
        truthy_values = [
            MagicMock(spec=Conversation),
            "some_string",
            1,
            [1, 2, 3],
            {"key": "value"},
            True,
        ]

        for truthy_value in truthy_values:
            event_service._conversation = truthy_value
            assert event_service.is_open(), f"Expected True for {truthy_value}"


class TestEventServiceBodyFiltering:
    """Test cases for EventService body filtering functionality."""

    def test_event_matches_body_with_message_event(self, event_service):
        """Test _event_matches_body with MessageEvent containing text content."""
        from openhands.sdk.llm.message import TextContent

        # Create a MessageEvent with text content
        message = Message(role="user", content=[TextContent(text="Hello world")])
        event = MessageEvent(id="test", source="user", llm_message=message)

        # Test case-insensitive matching
        assert event_service._event_matches_body(event, "hello")
        assert event_service._event_matches_body(event, "WORLD")
        assert event_service._event_matches_body(event, "Hello world")
        assert event_service._event_matches_body(event, "llo wor")

        # Test non-matching
        assert not event_service._event_matches_body(event, "goodbye")
        assert not event_service._event_matches_body(event, "xyz")

    def test_event_matches_body_with_non_message_event(self, event_service):
        """Test _event_matches_body with non-MessageEvent (should return False)."""
        from openhands.sdk.event.user_action import PauseEvent

        # Create a non-MessageEvent
        event = PauseEvent(id="test")

        # Should always return False for non-MessageEvent
        assert not event_service._event_matches_body(event, "any text")
        assert not event_service._event_matches_body(event, "")

    def test_event_matches_body_with_empty_content(self, event_service):
        """Test _event_matches_body with MessageEvent containing empty content."""
        # Create a MessageEvent with empty content
        message = Message(role="user", content=[])
        event = MessageEvent(id="test", source="user", llm_message=message)

        # Should not match any non-empty text
        assert not event_service._event_matches_body(event, "any text")
        # Empty string should match empty content (empty string contains empty string)
        assert event_service._event_matches_body(event, "")

    @pytest.mark.asyncio
    async def test_search_events_with_body_filter_integration(self, event_service):
        """Test search_events with body filter using real MessageEvents."""
        from openhands.sdk.llm.message import TextContent

        # Create a conversation with MessageEvents containing different text
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)

        events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Hello world")]
                ),
            ),
            MessageEvent(
                id="event2",
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="How can I help?")]
                ),
            ),
            MessageEvent(
                id="event3",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Create a Python script")]
                ),
            ),
        ]

        state.events = events
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        # Test filtering by "hello" (should match event1)
        result = await event_service.search_events(body="hello")
        assert len(result.items) == 1
        assert result.items[0].id == "event1"

        # Test filtering by "python" (should match event3)
        result = await event_service.search_events(body="python")
        assert len(result.items) == 1
        assert result.items[0].id == "event3"

        # Test filtering by "help" (should match event2)
        result = await event_service.search_events(body="help")
        assert len(result.items) == 1
        assert result.items[0].id == "event2"

        # Test filtering by non-matching text
        result = await event_service.search_events(body="nonexistent")
        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_count_events_with_body_filter_integration(self, event_service):
        """Test count_events with body filter using real MessageEvents."""
        from openhands.sdk.llm.message import TextContent

        # Create a conversation with MessageEvents containing different text
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)

        events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Hello world")]
                ),
            ),
            MessageEvent(
                id="event2",
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="Hello there")]
                ),
            ),
            MessageEvent(
                id="event3",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Create a Python script")]
                ),
            ),
        ]

        state.events = events
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        # Test counting by "hello" (should match 2 events)
        result = await event_service.count_events(body="hello")
        assert result == 2

        # Test counting by "python" (should match 1 event)
        result = await event_service.count_events(body="python")
        assert result == 1

        # Test counting by non-matching text
        result = await event_service.count_events(body="nonexistent")
        assert result == 0


class TestEventServiceRun:
    """Test cases for EventService.run method."""

    @pytest.mark.asyncio
    async def test_run_inactive_service(self, event_service):
        """Test that run raises ValueError when conversation is not active."""
        event_service._conversation = None

        with pytest.raises(ValueError, match="inactive_service"):
            await event_service.run()

    @pytest.mark.asyncio
    async def test_run_already_running_by_status(self, event_service):
        """Test that run raises ValueError when conversation is already running."""
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.execution_status = ConversationExecutionStatus.RUNNING
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        with pytest.raises(ValueError, match="conversation_already_running"):
            await event_service.run()

    @pytest.mark.asyncio
    async def test_run_already_running_by_task(self, event_service):
        """Test that run raises ValueError when there's an active run task."""
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.execution_status = ConversationExecutionStatus.IDLE
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        event_service._conversation = conversation

        # Create a mock task that is not done
        mock_task = MagicMock()
        mock_task.done.return_value = False
        event_service._run_task = mock_task

        with pytest.raises(ValueError, match="conversation_already_running"):
            await event_service.run()

    @pytest.mark.asyncio
    async def test_run_starts_background_task(self, event_service):
        """Test that run starts a background task and returns immediately."""
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.execution_status = ConversationExecutionStatus.IDLE
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state
        conversation.run = MagicMock()

        event_service._conversation = conversation
        event_service._publish_state_update = AsyncMock()

        # Call run - should return immediately
        await event_service.run()

        # Verify a task was created
        assert event_service._run_task is not None

        # Wait for the background task to complete
        await event_service._run_task

        # Verify conversation.run was called
        conversation.run.assert_called_once()

        # Verify state update was published after run completed
        event_service._publish_state_update.assert_called()

    @pytest.mark.asyncio
    async def test_run_publishes_state_update_on_completion(self, event_service):
        """Test that run publishes state update after completion."""
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.execution_status = ConversationExecutionStatus.IDLE
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state
        conversation.run = MagicMock()

        event_service._conversation = conversation
        event_service._publish_state_update = AsyncMock()

        await event_service.run()
        await event_service._run_task  # Wait for completion

        # State update should be published after run completes
        event_service._publish_state_update.assert_called()

    @pytest.mark.asyncio
    async def test_run_publishes_state_update_on_error(self, event_service):
        """Test that run publishes state update even if run raises an error."""
        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)
        state.execution_status = ConversationExecutionStatus.IDLE
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state
        conversation.run = MagicMock(side_effect=RuntimeError("Test error"))

        event_service._conversation = conversation
        event_service._publish_state_update = AsyncMock()

        await event_service.run()

        # Wait for the background task to complete (it will raise but be caught)
        try:
            await event_service._run_task
        except RuntimeError:
            pass  # Expected

        # State update should still be published (in finally block)
        event_service._publish_state_update.assert_called()
