"""
Standalone unit tests for PubSub class functionality.

This test file recreates the PubSub class logic to test it
without dependencies on the openhands.sdk module.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest


# Mock Event class
class MockEvent:
    """Mock Event class for testing purposes."""

    def __init__(self, event_type="test_event", data="test_data"):
        self.type: str = event_type
        self.data: str = data

    def model_dump(self):
        return {"type": self.type, "data": self.data}


# Mock logger
class MockLogger:
    """Mock logger for testing purposes."""

    def __init__(self):
        self.debug_calls: list[Any] = []
        self.warning_calls: list[Any] = []
        self.error_calls: list[Any] = []

    def debug(self, message):
        self.debug_calls.append(message)

    def warning(self, message):
        self.warning_calls.append(message)

    def error(self, message, exc_info=False):
        self.error_calls.append((message, exc_info))


# Recreate Subscriber ABC for testing
class SubscriberForTesting(ABC):
    @abstractmethod
    async def __call__(self, event):
        """Invoke this subscriber"""

    async def close(self):
        """Clean up this subscriber"""


# Recreate PubSub for testing
@dataclass
class PubSubForTesting:
    """Testable version of PubSub without external dependencies."""

    _subscribers: dict[UUID, SubscriberForTesting] = field(default_factory=dict)
    _logger: MockLogger = field(default_factory=MockLogger)

    def subscribe(self, subscriber: SubscriberForTesting) -> UUID:
        """Subscribe a subscriber and return its UUID for later unsubscription."""
        subscriber_id = uuid4()
        self._subscribers[subscriber_id] = subscriber
        self._logger.debug(f"Subscribed subscriber with ID: {subscriber_id}")
        return subscriber_id

    def unsubscribe(self, subscriber_id: UUID) -> bool:
        """Unsubscribe a subscriber by its UUID."""
        if subscriber_id in self._subscribers:
            del self._subscribers[subscriber_id]
            self._logger.debug(f"Unsubscribed subscriber with ID: {subscriber_id}")
            return True
        else:
            self._logger.warning(
                f"Attempted to unsubscribe unknown subscriber ID: {subscriber_id}"
            )
            return False

    async def __call__(self, event) -> None:
        """Invoke all registered callbacks with the given event."""
        for subscriber_id, subscriber in list(self._subscribers.items()):
            try:
                await subscriber(event)
            except Exception as e:
                self._logger.error(
                    f"Error in subscriber {subscriber_id}: {e}", exc_info=True
                )

    async def close(self):
        await asyncio.gather(
            *[subscriber.close() for subscriber in self._subscribers.values()],
            return_exceptions=True,
        )
        self._subscribers.clear()


# Mock Subscriber class for testing
class MockSubscriber(SubscriberForTesting):
    """Mock Subscriber for testing purposes."""

    def __init__(self, name="test_subscriber"):
        self.name: str = name
        self.call_count: int = 0
        self.received_events: list[Any] = []
        self.close_called: bool = False
        self.should_raise_error: bool = False
        self.error_to_raise: Exception | None = None

    async def __call__(self, event):
        """Invoke this subscriber"""
        self.call_count += 1
        self.received_events.append(event)

        if self.should_raise_error:
            raise self.error_to_raise or Exception(f"Error in {self.name}")

    async def close(self):
        """Clean up this subscriber"""
        self.close_called: bool = True


@pytest.fixture
def pubsub():
    """Create a PubSub instance for testing."""
    return PubSubForTesting()


@pytest.fixture
def sample_event():
    """Create a sample Event for testing."""
    return MockEvent("test_event", "test_data")


@pytest.fixture
def sample_events():
    """Create multiple sample Events for testing."""
    events = []
    for i in range(3):
        events.append(MockEvent(f"test_event_{i}", f"test_data_{i}"))
    return events


@pytest.fixture
def mock_subscriber():
    """Create a mock subscriber for testing."""
    return MockSubscriber("subscriber_1")


@pytest.fixture
def mock_subscribers():
    """Create multiple mock subscribers for testing."""
    return [
        MockSubscriber("subscriber_1"),
        MockSubscriber("subscriber_2"),
        MockSubscriber("subscriber_3"),
    ]


class TestPubSubSubscribe:
    """Test cases for PubSub.subscribe method."""

    def test_subscribe_single_subscriber(self, pubsub, mock_subscriber):
        """Test subscribing a single subscriber."""
        subscriber_id = pubsub.subscribe(mock_subscriber)

        # Should return a UUID
        assert isinstance(subscriber_id, UUID)

        # Subscriber should be in the internal dict
        assert subscriber_id in pubsub._subscribers
        assert pubsub._subscribers[subscriber_id] == mock_subscriber

        # Should have exactly one subscriber
        assert len(pubsub._subscribers) == 1

        # Should log the subscription
        assert len(pubsub._logger.debug_calls) == 1
        assert "Subscribed subscriber with ID" in pubsub._logger.debug_calls[0]

    def test_subscribe_multiple_subscribers(self, pubsub, mock_subscribers):
        """Test subscribing multiple subscribers."""
        subscriber_ids = []

        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

            # Each should return a unique UUID
            assert isinstance(subscriber_id, UUID)
            assert subscriber_id not in subscriber_ids[:-1]  # Unique from previous IDs

        # All subscribers should be in the dict
        assert len(pubsub._subscribers) == len(mock_subscribers)

        for i, subscriber_id in enumerate(subscriber_ids):
            assert pubsub._subscribers[subscriber_id] == mock_subscribers[i]

    def test_subscribe_same_subscriber_multiple_times(self, pubsub, mock_subscriber):
        """Test subscribing the same subscriber instance multiple times."""
        subscriber_id_1 = pubsub.subscribe(mock_subscriber)
        subscriber_id_2 = pubsub.subscribe(mock_subscriber)

        # Should get different UUIDs
        assert subscriber_id_1 != subscriber_id_2

        # Both should be in the dict
        assert len(pubsub._subscribers) == 2
        assert pubsub._subscribers[subscriber_id_1] == mock_subscriber
        assert pubsub._subscribers[subscriber_id_2] == mock_subscriber

    def test_subscribe_returns_unique_uuids(self, pubsub):
        """Test that subscribe always returns unique UUIDs."""
        subscribers = [MockSubscriber(f"subscriber_{i}") for i in range(10)]
        subscriber_ids = []

        for subscriber in subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        # All IDs should be unique
        assert len(set(subscriber_ids)) == len(subscriber_ids)


class TestPubSubUnsubscribe:
    """Test cases for PubSub.unsubscribe method."""

    def test_unsubscribe_existing_subscriber(self, pubsub, mock_subscriber):
        """Test unsubscribing an existing subscriber."""
        subscriber_id = pubsub.subscribe(mock_subscriber)

        # Unsubscribe should return True
        result = pubsub.unsubscribe(subscriber_id)
        assert result is True

        # Subscriber should be removed from dict
        assert subscriber_id not in pubsub._subscribers
        assert len(pubsub._subscribers) == 0

        # Should log the unsubscription
        assert len(pubsub._logger.debug_calls) == 2  # Subscribe + unsubscribe
        assert "Unsubscribed subscriber with ID" in pubsub._logger.debug_calls[1]

    def test_unsubscribe_nonexistent_subscriber(self, pubsub):
        """Test unsubscribing a non-existent subscriber."""
        fake_id = uuid4()

        # Unsubscribe should return False
        result = pubsub.unsubscribe(fake_id)
        assert result is False

        # Dict should remain empty
        assert len(pubsub._subscribers) == 0

        # Should log the warning
        assert len(pubsub._logger.warning_calls) == 1
        assert (
            "Attempted to unsubscribe unknown subscriber ID"
            in pubsub._logger.warning_calls[0]
        )

    def test_unsubscribe_multiple_subscribers(self, pubsub, mock_subscribers):
        """Test unsubscribing multiple subscribers."""
        subscriber_ids = []

        # Subscribe all
        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        assert len(pubsub._subscribers) == len(mock_subscribers)

        # Unsubscribe first subscriber
        result = pubsub.unsubscribe(subscriber_ids[0])
        assert result is True
        assert len(pubsub._subscribers) == len(mock_subscribers) - 1
        assert subscriber_ids[0] not in pubsub._subscribers

        # Other subscribers should still be there
        for i in range(1, len(subscriber_ids)):
            assert subscriber_ids[i] in pubsub._subscribers

    def test_unsubscribe_already_unsubscribed(self, pubsub, mock_subscriber):
        """Test unsubscribing a subscriber that was already unsubscribed."""
        subscriber_id = pubsub.subscribe(mock_subscriber)

        # First unsubscribe should succeed
        result1 = pubsub.unsubscribe(subscriber_id)
        assert result1 is True

        # Second unsubscribe should fail
        result2 = pubsub.unsubscribe(subscriber_id)
        assert result2 is False

    def test_unsubscribe_partial_removal(self, pubsub, mock_subscribers):
        """Test that unsubscribing one subscriber doesn't affect others."""
        subscriber_ids = []

        # Subscribe all
        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        # Unsubscribe middle subscriber
        middle_index = len(subscriber_ids) // 2
        result = pubsub.unsubscribe(subscriber_ids[middle_index])
        assert result is True

        # Check that only the middle subscriber was removed
        assert len(pubsub._subscribers) == len(mock_subscribers) - 1
        assert subscriber_ids[middle_index] not in pubsub._subscribers

        # All other subscribers should still be there
        for i, subscriber_id in enumerate(subscriber_ids):
            if i != middle_index:
                assert subscriber_id in pubsub._subscribers
                assert pubsub._subscribers[subscriber_id] == mock_subscribers[i]


class TestPubSubCall:
    """Test cases for PubSub.__call__ method (event distribution)."""

    @pytest.mark.asyncio
    async def test_call_with_no_subscribers(self, pubsub, sample_event):
        """Test calling PubSub with no subscribers."""
        # Should not raise any errors
        await pubsub(sample_event)

    @pytest.mark.asyncio
    async def test_call_with_single_subscriber(
        self, pubsub, mock_subscriber, sample_event
    ):
        """Test calling PubSub with a single subscriber."""
        pubsub.subscribe(mock_subscriber)

        await pubsub(sample_event)

        # Subscriber should have received the event
        assert mock_subscriber.call_count == 1
        assert len(mock_subscriber.received_events) == 1
        assert mock_subscriber.received_events[0] == sample_event

    @pytest.mark.asyncio
    async def test_call_with_multiple_subscribers(
        self, pubsub, mock_subscribers, sample_event
    ):
        """Test calling PubSub with multiple subscribers."""
        # Subscribe all
        for subscriber in mock_subscribers:
            pubsub.subscribe(subscriber)

        await pubsub(sample_event)

        # All subscribers should have received the event
        for subscriber in mock_subscribers:
            assert subscriber.call_count == 1
            assert len(subscriber.received_events) == 1
            assert subscriber.received_events[0] == sample_event

    @pytest.mark.asyncio
    async def test_call_with_multiple_events(
        self, pubsub, mock_subscriber, sample_events
    ):
        """Test calling PubSub multiple times with different events."""
        pubsub.subscribe(mock_subscriber)

        for event in sample_events:
            await pubsub(event)

        # Subscriber should have received all events
        assert mock_subscriber.call_count == len(sample_events)
        assert len(mock_subscriber.received_events) == len(sample_events)
        assert mock_subscriber.received_events == sample_events

    @pytest.mark.asyncio
    async def test_call_distributes_to_all_current_subscribers(
        self, pubsub, mock_subscribers, sample_event
    ):
        """Test that events are distributed to all current subscribers."""
        subscriber_ids = []

        # Subscribe all
        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        await pubsub(sample_event)

        # All should have received the event
        for subscriber in mock_subscribers:
            assert subscriber.call_count == 1
            assert sample_event in subscriber.received_events

    @pytest.mark.asyncio
    async def test_call_with_subscriber_error_isolation(
        self, pubsub, mock_subscribers, sample_event
    ):
        """Test that one subscriber's error doesn't affect others."""
        # Subscribe all
        for subscriber in mock_subscribers:
            pubsub.subscribe(subscriber)

        # Make the middle subscriber raise an error
        middle_subscriber = mock_subscribers[len(mock_subscribers) // 2]
        middle_subscriber.should_raise_error = True
        middle_subscriber.error_to_raise = ValueError("Test error")

        # Should not raise an exception
        await pubsub(sample_event)

        # All subscribers should have been called (including the failing one)
        for subscriber in mock_subscribers:
            assert subscriber.call_count == 1
            assert sample_event in subscriber.received_events

        # Error should be logged
        assert len(pubsub._logger.error_calls) == 1
        assert "Error in subscriber" in pubsub._logger.error_calls[0][0]
        assert pubsub._logger.error_calls[0][1] is True  # exc_info=True


class TestPubSubEventIsolation:
    """Test cases ensuring removed subscribers don't receive events."""

    @pytest.mark.asyncio
    async def test_unsubscribed_subscriber_no_events(
        self, pubsub, mock_subscribers, sample_event
    ):
        """Test that unsubscribed subscribers don't receive events."""
        subscriber_ids = []

        # Subscribe all
        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        # Unsubscribe first subscriber
        pubsub.unsubscribe(subscriber_ids[0])

        await pubsub(sample_event)

        # First subscriber should not have received the event
        assert mock_subscribers[0].call_count == 0
        assert len(mock_subscribers[0].received_events) == 0

        # Other subscribers should have received the event
        for i in range(1, len(mock_subscribers)):
            assert mock_subscribers[i].call_count == 1
            assert sample_event in mock_subscribers[i].received_events

    @pytest.mark.asyncio
    async def test_unsubscribe_during_event_processing(
        self, pubsub, mock_subscribers, sample_events
    ):
        """Test unsubscribing between events."""
        subscriber_ids = []

        # Subscribe all
        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        # Send first event
        await pubsub(sample_events[0])

        # All should have received first event
        for subscriber in mock_subscribers:
            assert subscriber.call_count == 1
            assert sample_events[0] in subscriber.received_events

        # Unsubscribe middle subscriber
        middle_index = len(subscriber_ids) // 2
        pubsub.unsubscribe(subscriber_ids[middle_index])

        # Send second event
        await pubsub(sample_events[1])

        # Middle subscriber should not have received second event
        middle_subscriber = mock_subscribers[middle_index]
        assert middle_subscriber.call_count == 1  # Only first event
        assert len(middle_subscriber.received_events) == 1
        assert sample_events[1] not in middle_subscriber.received_events

        # Other subscribers should have received both events
        for i, subscriber in enumerate(mock_subscribers):
            if i != middle_index:
                assert subscriber.call_count == 2
                assert sample_events[0] in subscriber.received_events
                assert sample_events[1] in subscriber.received_events

    @pytest.mark.asyncio
    async def test_resubscribe_after_unsubscribe(
        self, pubsub, mock_subscriber, sample_events
    ):
        """Test resubscribing a subscriber after unsubscribing."""
        # Subscribe
        subscriber_id_1 = pubsub.subscribe(mock_subscriber)

        # Send first event
        await pubsub(sample_events[0])
        assert mock_subscriber.call_count == 1

        # Unsubscribe
        pubsub.unsubscribe(subscriber_id_1)

        # Send second event (should not be received)
        await pubsub(sample_events[1])
        assert mock_subscriber.call_count == 1  # Still 1

        # Resubscribe with new ID
        subscriber_id_2 = pubsub.subscribe(mock_subscriber)
        assert subscriber_id_2 != subscriber_id_1  # Different ID

        # Send third event (should be received)
        await pubsub(sample_events[2])
        assert mock_subscriber.call_count == 2  # Now 2


class TestPubSubClose:
    """Test cases for PubSub.close method."""

    @pytest.mark.asyncio
    async def test_close_with_no_subscribers(self, pubsub):
        """Test closing PubSub with no subscribers."""
        # Should not raise any errors
        await pubsub.close()
        assert len(pubsub._subscribers) == 0

    @pytest.mark.asyncio
    async def test_close_with_single_subscriber(self, pubsub, mock_subscriber):
        """Test closing PubSub with a single subscriber."""
        pubsub.subscribe(mock_subscriber)

        await pubsub.close()

        # Subscriber's close method should have been called
        assert mock_subscriber.close_called is True

        # Subscribers dict should be cleared
        assert len(pubsub._subscribers) == 0

    @pytest.mark.asyncio
    async def test_close_with_multiple_subscribers(self, pubsub, mock_subscribers):
        """Test closing PubSub with multiple subscribers."""
        # Subscribe all
        for subscriber in mock_subscribers:
            pubsub.subscribe(subscriber)

        await pubsub.close()

        # All subscribers' close methods should have been called
        for subscriber in mock_subscribers:
            assert subscriber.close_called is True

        # Subscribers dict should be cleared
        assert len(pubsub._subscribers) == 0

    @pytest.mark.asyncio
    async def test_close_only_calls_current_subscribers(self, pubsub, mock_subscribers):
        """Test that close only calls close on current subscribers,
        not unsubscribed ones."""
        subscriber_ids = []

        # Subscribe all
        for subscriber in mock_subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        # Unsubscribe first subscriber
        pubsub.unsubscribe(subscriber_ids[0])

        await pubsub.close()

        # First subscriber's close should NOT have been called
        assert mock_subscribers[0].close_called is False

        # Other subscribers' close methods should have been called
        for i in range(1, len(mock_subscribers)):
            assert mock_subscribers[i].close_called is True

        # Subscribers dict should be cleared
        assert len(pubsub._subscribers) == 0

    @pytest.mark.asyncio
    async def test_close_handles_subscriber_close_errors(
        self, pubsub, mock_subscribers
    ):
        """Test that close handles errors in subscriber close methods."""
        # Subscribe all
        for subscriber in mock_subscribers:
            pubsub.subscribe(subscriber)

        # Make one subscriber's close method raise an error
        async def failing_close():
            raise ValueError("Close error")

        mock_subscribers[1].close = failing_close

        # Should not raise an exception (asyncio.gather handles it)
        await pubsub.close()

        # Other subscribers should still have their close called
        assert mock_subscribers[0].close_called is True
        assert mock_subscribers[2].close_called is True

        # Subscribers dict should be cleared
        assert len(pubsub._subscribers) == 0

    @pytest.mark.asyncio
    async def test_close_concurrent_execution(self, pubsub):
        """Test that close calls all subscriber close methods concurrently."""
        # Create subscribers with async close methods that track timing
        close_times = []

        async def timed_close(subscriber_id):
            await asyncio.sleep(0.1)  # Simulate some work
            close_times.append(subscriber_id)

        subscribers = []
        for i in range(3):
            subscriber = MockSubscriber(f"subscriber_{i}")
            subscriber.close = lambda sid=i: timed_close(sid)
            subscribers.append(subscriber)
            pubsub.subscribe(subscriber)

        start_time = asyncio.get_event_loop().time()
        await pubsub.close()
        end_time = asyncio.get_event_loop().time()

        # Should complete in roughly 0.1 seconds (concurrent)
        # rather than 0.3 (sequential)
        elapsed_time = end_time - start_time
        assert elapsed_time < 0.2  # Allow some margin for test execution overhead

        # All close methods should have been called
        assert len(close_times) == 3


class TestPubSubErrorHandling:
    """Test cases for error handling in PubSub."""

    @pytest.mark.asyncio
    async def test_subscriber_exception_isolation(
        self, pubsub, mock_subscribers, sample_event
    ):
        """Test that exceptions in one subscriber don't affect others."""
        # Subscribe all
        for subscriber in mock_subscribers:
            pubsub.subscribe(subscriber)

        # Make multiple subscribers raise different errors
        mock_subscribers[0].should_raise_error = True
        mock_subscribers[0].error_to_raise = ValueError("First error")

        mock_subscribers[2].should_raise_error = True
        mock_subscribers[2].error_to_raise = RuntimeError("Third error")

        # Should not raise an exception
        await pubsub(sample_event)

        # All subscribers should have been called
        for subscriber in mock_subscribers:
            assert subscriber.call_count == 1
            assert sample_event in subscriber.received_events

        # Both errors should be logged
        assert len(pubsub._logger.error_calls) == 2

    @pytest.mark.asyncio
    async def test_multiple_events_with_errors(
        self, pubsub, mock_subscriber, sample_events
    ):
        """Test that errors in one event don't prevent processing
        of subsequent events."""
        pubsub.subscribe(mock_subscriber)

        # Make subscriber fail on second event only by setting the error flag
        # This way the error handling in PubSub will catch it

        # Process all events
        for i, event in enumerate(sample_events):
            if i == 1:  # Second event should cause error
                mock_subscriber.should_raise_error = True
                mock_subscriber.error_to_raise = ValueError("Second event error")
            else:
                mock_subscriber.should_raise_error = False
                mock_subscriber.error_to_raise = None

            await pubsub(event)

        # All events should have been processed
        assert len(mock_subscriber.received_events) == len(sample_events)
        assert mock_subscriber.received_events == sample_events

        # One error should be logged
        assert len(pubsub._logger.error_calls) == 1


class TestPubSubIntegration:
    """Integration test cases for PubSub."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, pubsub, sample_events):
        """Test complete PubSub lifecycle: subscribe, events, unsubscribe, close."""
        subscribers = [MockSubscriber(f"subscriber_{i}") for i in range(3)]
        subscriber_ids = []

        # Subscribe all
        for subscriber in subscribers:
            subscriber_id = pubsub.subscribe(subscriber)
            subscriber_ids.append(subscriber_id)

        # Send first event to all
        await pubsub(sample_events[0])

        # All should receive first event
        for subscriber in subscribers:
            assert subscriber.call_count == 1
            assert sample_events[0] in subscriber.received_events

        # Unsubscribe middle subscriber
        pubsub.unsubscribe(subscriber_ids[1])

        # Send second event
        await pubsub(sample_events[1])

        # Only first and third should receive second event
        assert subscribers[0].call_count == 2
        assert subscribers[1].call_count == 1  # Still 1
        assert subscribers[2].call_count == 2

        # Close PubSub
        await pubsub.close()

        # Only current subscribers should have close called
        assert subscribers[0].close_called is True
        assert subscribers[1].close_called is False  # Was unsubscribed
        assert subscribers[2].close_called is True

        # Dict should be empty
        assert len(pubsub._subscribers) == 0

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe(self, pubsub, sample_event):
        """Test concurrent subscribe/unsubscribe operations."""
        subscribers = [MockSubscriber(f"subscriber_{i}") for i in range(10)]

        # Subscribe all concurrently
        subscribe_tasks = [
            asyncio.create_task(asyncio.to_thread(pubsub.subscribe, subscriber))
            for subscriber in subscribers
        ]
        subscriber_ids = await asyncio.gather(*subscribe_tasks)

        # All should be subscribed
        assert len(pubsub._subscribers) == len(subscribers)

        # Send event
        await pubsub(sample_event)

        # All should receive event
        for subscriber in subscribers:
            assert subscriber.call_count == 1

        # Unsubscribe half concurrently
        unsubscribe_tasks = [
            asyncio.create_task(
                asyncio.to_thread(pubsub.unsubscribe, subscriber_ids[i])
            )
            for i in range(0, len(subscriber_ids), 2)
        ]
        results = await asyncio.gather(*unsubscribe_tasks)

        # All unsubscribe operations should succeed
        assert all(results)

        # Half should remain subscribed
        assert len(pubsub._subscribers) == len(subscribers) // 2

    @pytest.mark.asyncio
    async def test_stress_test_many_subscribers(self, pubsub, sample_event):
        """Stress test with many subscribers."""
        num_subscribers = 100
        subscribers = [
            MockSubscriber(f"subscriber_{i}") for i in range(num_subscribers)
        ]

        # Subscribe all
        for subscriber in subscribers:
            pubsub.subscribe(subscriber)

        # Send event
        await pubsub(sample_event)

        # All should receive event
        for subscriber in subscribers:
            assert subscriber.call_count == 1
            assert sample_event in subscriber.received_events

        # Close should handle all subscribers
        await pubsub.close()

        # All should have close called
        for subscriber in subscribers:
            assert subscriber.close_called is True

        assert len(pubsub._subscribers) == 0
