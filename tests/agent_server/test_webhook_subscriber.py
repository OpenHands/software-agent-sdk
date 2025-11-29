"""
Standalone unit tests for WebhookSubscriber class functionality.

This test file recreates the WebhookSubscriber class logic to test it
without dependencies on the openhands.sdk module.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.config import WebhookSpec
from openhands.agent_server.conversation_service import WebhookSubscriber
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def mock_event_service():
    """Create a mock EventService for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        # Mock httpx.get to prevent HTTP calls to staging server during LLM init
        with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(json=lambda: {"data": []})
            service = EventService(
                stored=StoredConversation(
                    id=uuid4(),
                    agent=Agent(
                        llm=LLM(
                            usage_id="test-llm",
                            model="test-model",
                            api_key=SecretStr("test-key"),
                        ),
                        tools=[],
                    ),
                    workspace=LocalWorkspace(working_dir="workspace/project"),
                ),
                conversations_dir=temp_path / "conversations_dir",
            )
            yield service


@pytest.fixture
def webhook_spec():
    """Create a WebhookSpec for testing."""
    return WebhookSpec(
        base_url="https://example.com",
        event_buffer_size=3,
        headers={"Content-Type": "application/json", "Authorization": "Bearer token"},
        num_retries=2,
        retry_delay=1,
        flush_delay=0.1,  # Short delay for testing
    )


@pytest.fixture
def minimal_webhook_spec():
    """Create a minimal WebhookSpec for testing."""
    return WebhookSpec(base_url="https://example.com")


@pytest.fixture
def sample_event():
    """Create a sample Event for testing."""
    text_content = TextContent(text="Hello, world!")
    message = Message(role="user", content=[text_content])
    message_event = MessageEvent(source="user", llm_message=message)
    return message_event


@pytest.fixture
def sample_events():
    """Create multiple sample Events for testing."""
    events = []
    for i in range(5):
        text_content = TextContent(text="Hello, world!")
        message = Message(role="user", content=[text_content])
        message_event = MessageEvent(source="user", llm_message=message)
        events.append(message_event)
    return events


@pytest.fixture
def sample_conversation_id():
    """Create a sample conversation ID for testing."""
    return uuid4()


class TestWebhookSpecValidation:
    """Test cases for WebhookSpec validation."""

    def test_webhook_spec_default_flush_delay(self):
        """Test that WebhookSpec has a default flush_delay value."""
        spec = WebhookSpec(base_url="https://example.com")
        assert spec.flush_delay == 30.0

    def test_webhook_spec_custom_flush_delay(self):
        """Test that WebhookSpec accepts custom flush_delay values."""
        spec = WebhookSpec(base_url="https://example.com", flush_delay=60.0)
        assert spec.flush_delay == 60.0

    def test_webhook_spec_flush_delay_validation_positive(self):
        """Test that flush_delay must be positive."""
        with pytest.raises(ValidationError) as exc_info:
            WebhookSpec(base_url="https://example.com", flush_delay=0.0)

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "greater_than"
        assert "flush_delay" in errors[0]["loc"]

    def test_webhook_spec_flush_delay_validation_negative(self):
        """Test that flush_delay cannot be negative."""
        with pytest.raises(ValidationError) as exc_info:
            WebhookSpec(base_url="https://example.com", flush_delay=-1.0)

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "greater_than"
        assert "flush_delay" in errors[0]["loc"]

    def test_webhook_spec_flush_delay_validation_small_positive(self):
        """Test that small positive flush_delay values are accepted."""
        spec = WebhookSpec(base_url="https://example.com", flush_delay=0.1)
        assert spec.flush_delay == 0.1


class TestWebhookSubscriberInitialization:
    """Test cases for WebhookSubscriber initialization."""

    def test_init_with_all_parameters(
        self, mock_event_service, webhook_spec, sample_conversation_id
    ):
        """Test initialization with all parameters."""
        session_api_key = "test_api_key"
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
            session_api_key=session_api_key,
        )

        assert subscriber.conversation_id == sample_conversation_id
        assert subscriber.service == mock_event_service
        assert subscriber.spec == webhook_spec
        assert subscriber.session_api_key == session_api_key
        assert subscriber.queue == []

    def test_init_without_session_api_key(
        self, mock_event_service, webhook_spec, sample_conversation_id
    ):
        """Test initialization without session API key."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        assert subscriber.conversation_id == sample_conversation_id
        assert subscriber.service == mock_event_service
        assert subscriber.spec == webhook_spec
        assert subscriber.session_api_key is None
        assert subscriber.queue == []

    def test_init_with_minimal_spec(
        self, mock_event_service, minimal_webhook_spec, sample_conversation_id
    ):
        """Test initialization with minimal webhook spec."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=minimal_webhook_spec,
        )

        assert subscriber.conversation_id == sample_conversation_id
        assert subscriber.service == mock_event_service
        assert subscriber.spec == minimal_webhook_spec
        assert subscriber.session_api_key is None
        assert subscriber.queue == []


class TestWebhookSubscriberCallMethod:
    """Test cases for WebhookSubscriber.__call__ method."""

    @pytest.mark.asyncio
    async def test_call_adds_event_to_queue(
        self, mock_event_service, webhook_spec, sample_event, sample_conversation_id
    ):
        """Test that calling the subscriber adds event to queue."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        await subscriber(sample_event)

        assert len(subscriber.queue) == 1
        assert subscriber.queue[0] == sample_event

    @pytest.mark.asyncio
    async def test_call_multiple_events_below_buffer_size(
        self, mock_event_service, webhook_spec, sample_events, sample_conversation_id
    ):
        """Test adding multiple events below buffer size."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add 2 events (buffer size is 3)
        for event in sample_events[:2]:
            await subscriber(event)

        assert len(subscriber.queue) == 2
        assert subscriber.queue == sample_events[:2]

    @pytest.mark.asyncio
    @patch.object(WebhookSubscriber, "_post_events")
    async def test_call_triggers_post_when_buffer_full(
        self,
        mock_post_events,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test that reaching buffer size triggers _post_events."""
        mock_post_events.return_value = None
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add events up to buffer size (3)
        for event in sample_events[:3]:
            await subscriber(event)

        # _post_events should be called once when buffer is full
        mock_post_events.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_triggers_post_multiple_times(
        self,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_event,
        sample_conversation_id,
    ):
        """Test that _post_events is called multiple times as buffer fills."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Mock the _post_events method to track calls but not actually post
        post_events_calls = []

        async def mock_post_events():
            post_events_calls.append(len(subscriber.queue))
            subscriber.queue.clear()  # Simulate clearing the queue

        subscriber._post_events = mock_post_events

        # Add 6 events (buffer size is 3, so should trigger twice:
        # at 3 events and at 6 events)
        for event in sample_events:  # 5 events
            await subscriber(event)

        # Add one more event to trigger the second post
        await subscriber(sample_event)

        # _post_events should be called twice (at 3 events and at 6 events)
        assert len(post_events_calls) == 2
        assert post_events_calls[0] == 3  # First call with 3 events
        assert post_events_calls[1] == 3  # Second call with 3 events


class TestWebhookSubscriberPostEvents:
    """Test cases for WebhookSubscriber._post_events method."""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_post_events_success(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test successful posting of events."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add events to queue
        subscriber.queue = sample_events[:3]

        await subscriber._post_events()

        # Verify HTTP request was made correctly
        expected_url = f"https://example.com/events/{sample_conversation_id.hex}"
        mock_client.request.assert_called_once_with(
            method="POST",
            url=expected_url,
            json=[event.model_dump() for event in sample_events[:3]],
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token",
            },
            timeout=30.0,
        )

        # Verify queue is cleared
        assert subscriber.queue == []

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_post_events_with_session_api_key(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test posting events with session API key."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
            session_api_key="test_session_key",
        )

        # Add events to queue
        subscriber.queue = sample_events[:2]

        await subscriber._post_events()

        # Verify session API key is added to headers
        expected_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer token",
            "X-Session-API-Key": "test_session_key",
        }
        expected_url = f"https://example.com/events/{sample_conversation_id.hex}"
        mock_client.request.assert_called_once_with(
            method="POST",
            url=expected_url,
            json=[event.model_dump() for event in sample_events[:2]],
            headers=expected_headers,
            timeout=30.0,
        )

    @pytest.mark.asyncio
    async def test_post_events_empty_queue(
        self, mock_event_service, webhook_spec, sample_conversation_id
    ):
        """Test posting events with empty queue."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Should return early without making HTTP request
        with patch("httpx.AsyncClient") as mock_client_class:
            await subscriber._post_events()
            mock_client_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_events_http_error_with_retries(
        self, mock_event_service, webhook_spec, sample_events, sample_conversation_id
    ):
        """Test HTTP error handling with retry logic."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add events to queue
        subscriber.queue = sample_events[:2]

        # Track retry attempts
        retry_attempts = []
        sleep_calls = []

        # Mock the HTTP client and sleep
        async def mock_request(*args, **kwargs):
            retry_attempts.append(len(retry_attempts) + 1)
            if len(retry_attempts) <= 2:  # Fail first two attempts
                raise httpx.HTTPStatusError(
                    "Server Error", request=MagicMock(), response=MagicMock()
                )
            # Third attempt succeeds - return a mock response
            response = AsyncMock()
            response.raise_for_status.return_value = None
            return response

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request = mock_request
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with patch("asyncio.sleep", side_effect=mock_sleep):
                await subscriber._post_events()

        # Verify retries were attempted
        assert len(retry_attempts) == 3
        assert len(sleep_calls) == 2  # Sleep between retries
        assert all(delay == webhook_spec.retry_delay for delay in sleep_calls)

        # Verify queue is cleared after success
        assert subscriber.queue == []

    @pytest.mark.asyncio
    async def test_post_events_max_retries_exceeded(
        self, mock_event_service, webhook_spec, sample_events, sample_conversation_id
    ):
        """Test behavior when max retries are exceeded."""
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add events to queue
        original_events = sample_events[:2]
        subscriber.queue = original_events.copy()

        # Track retry attempts
        retry_attempts = []
        sleep_calls = []

        # Mock the HTTP client to always fail
        async def mock_request(*args, **kwargs):
            retry_attempts.append(len(retry_attempts) + 1)
            raise httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=MagicMock()
            )

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request = mock_request
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with patch("asyncio.sleep", side_effect=mock_sleep):
                await subscriber._post_events()

        # Verify all retries were attempted (num_retries + 1 = 3 total attempts)
        assert len(retry_attempts) == 3
        assert len(sleep_calls) == 2

        # Verify events are re-queued after failure
        assert len(subscriber.queue) == 2
        assert subscriber.queue == original_events

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_post_events_handles_events_without_model_dump(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_conversation_id,
    ):
        """Test posting events that don't have model_dump method."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Create event without model_dump method
        event_without_model_dump = MagicMock()
        del event_without_model_dump.model_dump  # Remove model_dump method
        event_without_model_dump.__dict__ = {"type": "test", "data": "value"}

        subscriber.queue = [event_without_model_dump]

        await subscriber._post_events()

        # Verify __dict__ is used when model_dump is not available
        expected_url = f"https://example.com/events/{sample_conversation_id.hex}"
        mock_client.request.assert_called_once_with(
            method="POST",
            url=expected_url,
            json=[{"type": "test", "data": "value"}],
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token",
            },
            timeout=30.0,
        )


class TestWebhookSubscriberCloseMethod:
    """Test cases for WebhookSubscriber.close method."""

    @pytest.mark.asyncio
    @patch.object(WebhookSubscriber, "_post_events")
    async def test_close_posts_remaining_events(
        self,
        mock_post_events,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test that close method posts remaining events in queue."""
        mock_post_events.return_value = None
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add events to queue
        subscriber.queue = sample_events[:2]

        await subscriber.close()

        # Verify _post_events was called
        mock_post_events.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(WebhookSubscriber, "_post_events")
    async def test_close_with_empty_queue(
        self, mock_post_events, mock_event_service, webhook_spec, sample_conversation_id
    ):
        """Test close method with empty queue."""
        mock_post_events.return_value = None
        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        await subscriber.close()

        # _post_events should not be called when queue is empty
        mock_post_events.assert_not_called()


class TestWebhookSubscriberIntegration:
    """Integration test cases for WebhookSubscriber."""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_full_workflow(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test complete workflow from event addition to posting."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
            session_api_key="integration_test_key",
        )

        # Add events one by one (buffer size is 3)
        await subscriber(sample_events[0])
        assert len(subscriber.queue) == 1

        await subscriber(sample_events[1])
        assert len(subscriber.queue) == 2

        # This should trigger _post_events
        await subscriber(sample_events[2])
        assert len(subscriber.queue) == 0  # Queue should be cleared

        # Verify HTTP request was made
        mock_client.request.assert_called_once()

        # Add more events and close
        await subscriber(sample_events[3])
        await subscriber(sample_events[4])
        assert len(subscriber.queue) == 2

        await subscriber.close()
        assert len(subscriber.queue) == 0  # Queue should be cleared after close

        # Verify HTTP request was made again during close
        assert mock_client.request.call_count == 2

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_concurrent_event_processing(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test handling concurrent event additions."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Process events concurrently
        tasks = [subscriber(event) for event in sample_events]
        await asyncio.gather(*tasks)

        # With buffer size 3, we should have posted once and have 2 events remaining
        assert len(subscriber.queue) == 2
        mock_client.request.assert_called_once()

        # Close to post remaining events
        await subscriber.close()
        assert len(subscriber.queue) == 0
        assert mock_client.request.call_count == 2


class TestWebhookSubscriberErrorHandling:
    """Test cases for error handling in WebhookSubscriber."""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_network_error_handling(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test handling of network errors."""
        # Setup mock client to raise network error
        mock_client = AsyncMock()
        mock_client.request.side_effect = httpx.NetworkError("Connection failed")
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        subscriber.queue = sample_events[:2]

        with patch("asyncio.sleep") as mock_sleep:
            await subscriber._post_events()

        # Verify retries were attempted
        assert mock_client.request.call_count == 3  # num_retries + 1
        assert mock_sleep.call_count == 2

        # Events should be re-queued after failure
        assert len(subscriber.queue) == 2

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_timeout_error_handling(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test handling of timeout errors."""
        # Setup mock client to raise timeout error
        mock_client = AsyncMock()
        mock_client.request.side_effect = httpx.TimeoutException("Request timed out")
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        subscriber.queue = sample_events[:1]

        with patch("asyncio.sleep") as mock_sleep:
            await subscriber._post_events()

        # Verify retries were attempted
        assert mock_client.request.call_count == 3
        assert mock_sleep.call_count == 2

        # Events should be re-queued after failure
        assert len(subscriber.queue) == 1


class TestWebhookSubscriberFlushDelay:
    """Test cases for flush_delay functionality in WebhookSubscriber."""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_flush_delay_triggers_post(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_event,
        sample_conversation_id,
    ):
        """Test that flush_delay triggers posting after the specified delay."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add one event (below buffer size)
        await subscriber(sample_event)
        assert len(subscriber.queue) == 1

        # Wait for flush_delay to trigger
        await asyncio.sleep(webhook_spec.flush_delay + 0.05)

        # Verify HTTP request was made and queue is cleared
        mock_client.request.assert_called_once()
        assert len(subscriber.queue) == 0

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_flush_delay_not_reset_on_new_event(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test that flush_delay timer is NOT reset when new events are added."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add first event
        await subscriber(sample_events[0])
        assert len(subscriber.queue) == 1

        # Wait half the flush delay
        await asyncio.sleep(webhook_spec.flush_delay / 2)

        # Add second event (should NOT reset timer)
        await subscriber(sample_events[1])
        assert len(subscriber.queue) == 2

        # Wait another half delay (total time = flush_delay from first event)
        await asyncio.sleep(webhook_spec.flush_delay / 2 + 0.05)

        # Should have posted since timer was not reset
        mock_client.request.assert_called_once()
        assert len(subscriber.queue) == 0

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_flush_delay_cancelled_on_buffer_full(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_events,
        sample_conversation_id,
    ):
        """Test that flush_delay timer is cancelled when buffer becomes full."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add events up to buffer size - 1
        for event in sample_events[:2]:
            await subscriber(event)
        assert len(subscriber.queue) == 2

        # Add one more event to fill buffer (should trigger immediate post)
        await subscriber(sample_events[2])

        # Verify immediate post happened
        mock_client.request.assert_called_once()
        assert len(subscriber.queue) == 0

        # Wait for flush_delay to ensure timer was cancelled
        await asyncio.sleep(webhook_spec.flush_delay + 0.05)

        # Should not have made additional requests
        assert mock_client.request.call_count == 1

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_flush_delay_cancelled_on_close(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_event,
        sample_conversation_id,
    ):
        """Test that flush_delay timer is cancelled when subscriber is closed."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add one event
        await subscriber(sample_event)
        assert len(subscriber.queue) == 1

        # Close subscriber before flush_delay elapses
        await subscriber.close()

        # Verify close triggered post
        mock_client.request.assert_called_once()
        assert len(subscriber.queue) == 0

        # Wait for flush_delay to ensure timer was cancelled
        await asyncio.sleep(webhook_spec.flush_delay + 0.05)

        # Should not have made additional requests
        assert mock_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_flush_delay_no_post_when_queue_empty(
        self, mock_event_service, webhook_spec, sample_conversation_id
    ):
        """Test that flush_delay doesn't trigger post when queue is empty."""
        WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Wait for flush_delay
        await asyncio.sleep(webhook_spec.flush_delay + 0.05)

        # Should not have made any HTTP requests
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client_class.assert_not_called()

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_flush_delay_triggers_on_timer(
        self,
        mock_client_class,
        mock_event_service,
        webhook_spec,
        sample_event,
        sample_conversation_id,
    ):
        """Test that flush_delay timer triggers HTTP request."""
        # Setup mock client to succeed
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Add one event
        await subscriber(sample_event)
        assert len(subscriber.queue) == 1

        # Wait for flush_delay to trigger
        await asyncio.sleep(webhook_spec.flush_delay + 0.05)

        # Verify request was made and queue is cleared
        mock_client.request.assert_called_once()
        assert len(subscriber.queue) == 0


class TestConversationWebhookSubscriber:
    """Test cases for ConversationWebhookSubscriber class."""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_post_conversation_info_success(
        self, mock_client_class, webhook_spec, mock_event_service
    ):
        """Test successful posting of conversation info."""
        from openhands.agent_server.conversation_service import (
            ConversationWebhookSubscriber,
        )
        from openhands.agent_server.models import ConversationInfo
        from openhands.sdk.conversation.state import ConversationExecutionStatus

        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = ConversationWebhookSubscriber(
            spec=webhook_spec,
        )

        # Create sample conversation info
        conversation_info = ConversationInfo(
            id=uuid4(),
            agent=mock_event_service.stored.agent,
            workspace=mock_event_service.stored.workspace,
            created_at=utc_now(),
            updated_at=utc_now(),
            execution_status=ConversationExecutionStatus.RUNNING,
        )

        await subscriber.post_conversation_info(conversation_info)

        # Verify HTTP request was made correctly
        mock_client.request.assert_called_once_with(
            method="POST",
            url="https://example.com/conversations",
            json=conversation_info.model_dump(mode="json"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token",
            },
            timeout=30.0,
        )

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_post_conversation_info_with_session_api_key(
        self, mock_client_class, webhook_spec, mock_event_service
    ):
        """Test posting conversation info with session API key."""
        from openhands.agent_server.conversation_service import (
            ConversationWebhookSubscriber,
        )
        from openhands.agent_server.models import ConversationInfo
        from openhands.sdk.conversation.state import ConversationExecutionStatus

        # Setup mock client
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        subscriber = ConversationWebhookSubscriber(
            spec=webhook_spec,
            session_api_key="test_session_key",
        )

        # Create sample conversation info
        conversation_info = ConversationInfo(
            id=uuid4(),
            agent=mock_event_service.stored.agent,
            workspace=mock_event_service.stored.workspace,
            created_at=utc_now(),
            updated_at=utc_now(),
            execution_status=ConversationExecutionStatus.PAUSED,
        )

        await subscriber.post_conversation_info(conversation_info)

        # Verify session API key is added to headers
        expected_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer token",
            "X-Session-API-Key": "test_session_key",
        }
        mock_client.request.assert_called_once_with(
            method="POST",
            url="https://example.com/conversations",
            json=conversation_info.model_dump(mode="json"),
            headers=expected_headers,
            timeout=30.0,
        )

    @pytest.mark.asyncio
    async def test_post_conversation_info_http_error_with_retries(
        self, webhook_spec, mock_event_service
    ):
        """Test HTTP error handling with retry logic for conversation webhooks."""
        from openhands.agent_server.conversation_service import (
            ConversationWebhookSubscriber,
        )
        from openhands.agent_server.models import ConversationInfo
        from openhands.sdk.conversation.state import ConversationExecutionStatus

        subscriber = ConversationWebhookSubscriber(
            spec=webhook_spec,
        )

        # Create sample conversation info
        conversation_info = ConversationInfo(
            id=uuid4(),
            agent=mock_event_service.stored.agent,
            workspace=mock_event_service.stored.workspace,
            created_at=utc_now(),
            updated_at=utc_now(),
            execution_status=ConversationExecutionStatus.FINISHED,
        )

        # Track retry attempts
        retry_attempts = []
        sleep_calls = []

        # Mock the HTTP client and sleep
        async def mock_request(*args, **kwargs):
            retry_attempts.append(len(retry_attempts) + 1)
            if len(retry_attempts) <= 2:  # Fail first two attempts
                raise httpx.HTTPStatusError(
                    "Server Error", request=MagicMock(), response=MagicMock()
                )
            # Third attempt succeeds - return a mock response
            response = AsyncMock()
            response.raise_for_status.return_value = None
            return response

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request = mock_request
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with patch("asyncio.sleep", side_effect=mock_sleep):
                await subscriber.post_conversation_info(conversation_info)

        # Verify retries were attempted
        assert len(retry_attempts) == 3
        assert len(sleep_calls) == 2  # Sleep between retries
        assert all(delay == webhook_spec.retry_delay for delay in sleep_calls)


class TestWebhookSubscriberTimerBehavior:
    """Test cases for WebhookSubscriber timer behavior."""

    @pytest.mark.asyncio
    async def test_timer_not_reset_on_subsequent_events(
        self, mock_event_service, webhook_spec, sample_events, sample_conversation_id
    ):
        """Test that timer is not reset when new events are received."""
        # Use a longer flush delay for this test
        webhook_spec.flush_delay = 0.2

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Mock _post_events to track when it's called
        post_events_calls = []
        original_post_events = subscriber._post_events

        async def mock_post_events():
            post_events_calls.append(len(subscriber.queue))
            await original_post_events()

        subscriber._post_events = mock_post_events

        # Add first event - this should start the timer
        await subscriber(sample_events[0])
        assert subscriber._flush_timer is not None
        first_timer = subscriber._flush_timer

        # Add second event shortly after - timer should NOT be reset
        await asyncio.sleep(0.05)  # Small delay
        await subscriber(sample_events[1])

        # Timer should be the same instance (not reset)
        assert subscriber._flush_timer is first_timer
        assert len(subscriber.queue) == 2

        # Wait for timer to fire
        await asyncio.sleep(0.2)

        # Events should have been posted via timer
        assert len(post_events_calls) == 1
        assert post_events_calls[0] == 2  # Both events were posted

    @pytest.mark.asyncio
    async def test_timer_only_started_once_until_flush(
        self, mock_event_service, webhook_spec, sample_events, sample_conversation_id
    ):
        """Test that timer is only started once until events are flushed."""
        webhook_spec.flush_delay = 0.2

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Mock _post_events to prevent actual HTTP calls but clear the queue
        async def mock_post_events():
            subscriber.queue.clear()

        subscriber._post_events = mock_post_events

        # Add first event - should start timer
        await subscriber(sample_events[0])
        assert subscriber._flush_timer is not None
        first_timer = subscriber._flush_timer

        # Add more events - timer should remain the same
        await subscriber(sample_events[1])
        assert subscriber._flush_timer is first_timer

        # Wait for timer to complete and a bit more for cleanup
        await asyncio.sleep(0.3)

        # Timer should be cleared after flush
        assert subscriber._flush_timer is None

        # Add another event - should start a new timer
        await subscriber(sample_events[2])
        assert subscriber._flush_timer is not None
        assert subscriber._flush_timer is not first_timer  # New timer instance

    @pytest.mark.asyncio
    async def test_timer_cancelled_when_buffer_full(
        self, mock_event_service, webhook_spec, sample_events, sample_conversation_id
    ):
        """Test that timer is cancelled when buffer becomes full."""
        webhook_spec.flush_delay = 1.0  # Long delay
        webhook_spec.event_buffer_size = 2  # Small buffer

        subscriber = WebhookSubscriber(
            conversation_id=sample_conversation_id,
            service=mock_event_service,
            spec=webhook_spec,
        )

        # Mock _post_events to prevent actual HTTP calls
        subscriber._post_events = AsyncMock()

        # Add first event - should start timer
        await subscriber(sample_events[0])
        assert subscriber._flush_timer is not None
        timer = subscriber._flush_timer

        # Add second event to fill buffer - should cancel timer and post immediately
        await subscriber(sample_events[1])

        # Give a small delay for the cancellation to complete
        await asyncio.sleep(0.01)

        # Timer should be cancelled
        assert subscriber._flush_timer is None
        assert timer.cancelled()

        # _post_events should have been called immediately
        subscriber._post_events.assert_called_once()
