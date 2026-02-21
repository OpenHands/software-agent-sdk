"""Tests for websocket functionality in event_router.py"""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from openhands.agent_server.event_service import EventService
from openhands.agent_server.sockets import _WebSocketSubscriber
from openhands.sdk import Message
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm.message import TextContent


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket for testing."""
    websocket = MagicMock()
    websocket.accept = AsyncMock()
    websocket.receive_json = AsyncMock()
    websocket.send_json = AsyncMock()
    websocket.close = AsyncMock()
    websocket.application_state = MagicMock()
    return websocket


@pytest.fixture
def mock_event_service():
    """Create a mock EventService for testing."""
    service = MagicMock(spec=EventService)
    service.subscribe_to_events = AsyncMock(return_value=uuid4())
    service.unsubscribe_from_events = AsyncMock(return_value=True)
    service.send_message = AsyncMock()
    service.search_events = AsyncMock()
    return service


@pytest.fixture
def sample_conversation_id():
    """Return a sample conversation ID."""
    return uuid4()


class TestWebSocketSubscriber:
    """Test cases for _WebSocketSubscriber class."""

    @pytest.mark.asyncio
    async def test_websocket_subscriber_call_success(self, mock_websocket):
        """Test successful event sending through WebSocket subscriber."""
        subscriber = _WebSocketSubscriber(websocket=mock_websocket)
        event = MessageEvent(
            id="test_event",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="test")]),
        )

        await subscriber(event)

        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["id"] == "test_event"

    @pytest.mark.asyncio
    async def test_websocket_subscriber_call_exception(self, mock_websocket):
        """Test exception handling in WebSocket subscriber."""
        mock_websocket.send_json.side_effect = Exception("Connection error")
        subscriber = _WebSocketSubscriber(websocket=mock_websocket)
        event = MessageEvent(
            id="test_event",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="test")]),
        )

        # Should not raise exception, just log it
        await subscriber(event)

        mock_websocket.send_json.assert_called_once()


class TestWebSocketDisconnectHandling:
    """Test cases for WebSocket disconnect handling in the socket endpoint."""

    @pytest.mark.asyncio
    async def test_websocket_disconnect_breaks_loop(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that WebSocketDisconnect exception breaks the loop."""
        # Setup mock to raise WebSocketDisconnect on first receive_json call
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            # Mock config to not require authentication
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            # Import and call the socket function directly
            from openhands.agent_server.sockets import events_socket

            # This should not hang or loop infinitely
            await events_socket(
                sample_conversation_id, mock_websocket, session_api_key=None
            )

        # Verify that unsubscribe was called
        mock_event_service.unsubscribe_from_events.assert_called()

    @pytest.mark.asyncio
    async def test_websocket_no_double_unsubscription(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that unsubscription only happens once even with disconnect."""
        subscriber_id = uuid4()
        mock_event_service.subscribe_to_events.return_value = subscriber_id
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            # Mock config to not require authentication
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id, mock_websocket, session_api_key=None
            )

        # Should be called exactly once (not in both except and finally blocks)
        assert mock_event_service.unsubscribe_from_events.call_count == 1
        mock_event_service.unsubscribe_from_events.assert_called_with(subscriber_id)

    @pytest.mark.asyncio
    async def test_websocket_general_exception_continues_loop(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that general exceptions don't break the loop immediately."""
        call_count = 0

        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Some error")
            elif call_count == 2:
                raise WebSocketDisconnect()  # This should break the loop

        mock_websocket.receive_json.side_effect = side_effect

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            # Mock config to not require authentication
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id, mock_websocket, session_api_key=None
            )

        # Should have been called twice (once for ValueError, once for disconnect)
        assert mock_websocket.receive_json.call_count == 2
        mock_event_service.unsubscribe_from_events.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_successful_message_processing(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test successful message processing before disconnect."""
        message_data = {"role": "user", "content": "Hello"}
        call_count = 0

        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return message_data
            else:
                raise WebSocketDisconnect()

        mock_websocket.receive_json.side_effect = side_effect

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            # Mock config to not require authentication
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id, mock_websocket, session_api_key=None
            )

        # Should have processed the message
        mock_event_service.send_message.assert_called_once()
        args, kwargs = mock_event_service.send_message.call_args
        message = args[0]
        assert message.role == "user"
        assert len(message.content) == 1
        assert message.content[0].text == "Hello"
        # send_message only takes a message parameter, no run parameter

    @pytest.mark.asyncio
    async def test_websocket_unsubscribe_in_finally_when_no_disconnect(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that unsubscription happens in finally block when no disconnect."""
        # Simulate a different kind of exception that doesn't trigger disconnect handler
        mock_websocket.receive_json.side_effect = RuntimeError("Unexpected error")

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            # Mock config to not require authentication
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            # This should raise the RuntimeError but still clean up
            with pytest.raises(RuntimeError):
                await events_socket(
                    sample_conversation_id, mock_websocket, session_api_key=None
                )

        # Should still unsubscribe in the finally block
        mock_event_service.unsubscribe_from_events.assert_called_once()


class TestResendAllFunctionality:
    """Test cases for resend_all parameter functionality."""

    @pytest.mark.asyncio
    async def test_resend_all_false_no_resend(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that resend_all=False doesn't trigger event resend."""
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=False,
            )

        # search_events should not be called when not resending
        mock_event_service.search_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_resend_all_true_resends_events(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that resend_all=True resends all existing events."""
        # Create mock events to resend
        mock_events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Hello")]),
            ),
            MessageEvent(
                id="event2",
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="Hi")]),
            ),
        ]

        from typing import cast

        from openhands.agent_server.models import EventPage
        from openhands.sdk.event import Event

        mock_event_page = EventPage(
            items=cast(list[Event], mock_events), next_page_id=None
        )
        mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=True,
            )

        # search_events should be called to get all events
        mock_event_service.search_events.assert_called_once_with(
            page_id=None, timestamp__gte=None
        )

        # All events should be sent through websocket
        assert mock_websocket.send_json.call_count == 2
        sent_events = [call[0][0] for call in mock_websocket.send_json.call_args_list]
        assert sent_events[0]["id"] == "event1"
        assert sent_events[1]["id"] == "event2"

    @pytest.mark.asyncio
    async def test_resend_all_handles_search_events_exception(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that exceptions during search_events cause the WebSocket to fail."""
        mock_event_service.search_events = AsyncMock(
            side_effect=Exception("Search failed")
        )

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            # Should raise the exception from search_events
            with pytest.raises(Exception, match="Search failed"):
                await events_socket(
                    sample_conversation_id,
                    mock_websocket,
                    session_api_key=None,
                    resend_all=True,
                )

        # search_events should be called
        mock_event_service.search_events.assert_called_once()
        # WebSocket should be subscribed but then unsubscribed due to exception
        mock_event_service.subscribe_to_events.assert_called_once()
        mock_event_service.unsubscribe_from_events.assert_called_once()

    @pytest.mark.asyncio
    async def test_resend_all_handles_send_json_exception(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that exceptions during send_json are handled gracefully."""
        # Create mock events to resend
        mock_events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Hello")]),
            ),
        ]

        from typing import cast

        from openhands.agent_server.models import EventPage
        from openhands.sdk.event import Event

        mock_event_page = EventPage(
            items=cast(list[Event], mock_events), next_page_id=None
        )
        mock_event_service.search_events = AsyncMock(return_value=mock_event_page)

        # Make send_json fail during resend
        mock_websocket.send_json.side_effect = Exception("Send failed")
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            # Should not raise exception, should handle gracefully
            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=True,
            )

        # search_events should be called
        mock_event_service.search_events.assert_called_once()
        # send_json should be called (and fail)
        mock_websocket.send_json.assert_called_once()
        # WebSocket should still be subscribed and unsubscribed normally
        mock_event_service.subscribe_to_events.assert_called_once()
        mock_event_service.unsubscribe_from_events.assert_called_once()


class TestAfterTimestampFiltering:
    """Test cases for after_timestamp parameter functionality."""

    @pytest.mark.asyncio
    async def test_after_timestamp_passed_to_search_events(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that after_timestamp is normalized and passed to search_events."""
        from datetime import datetime
        from typing import cast

        from openhands.agent_server.models import EventPage
        from openhands.sdk.event import Event

        mock_events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Hello")]),
            ),
        ]
        mock_event_page = EventPage(
            items=cast(list[Event], mock_events), next_page_id=None
        )
        mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        # Use a naive timestamp (as would typically come from REST API response)
        test_timestamp = datetime(2024, 1, 15, 10, 30, 0)

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=True,
                after_timestamp=test_timestamp,
            )

        # search_events should be called with the (unchanged) naive timestamp
        mock_event_service.search_events.assert_called_once_with(
            page_id=None, timestamp__gte=test_timestamp
        )

    @pytest.mark.asyncio
    async def test_after_timestamp_timezone_aware_is_normalized(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that timezone-aware timestamps are normalized to naive server time."""
        from datetime import datetime
        from typing import cast

        from openhands.agent_server.models import EventPage
        from openhands.sdk.event import Event

        mock_events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Hello")]),
            ),
        ]
        mock_event_page = EventPage(
            items=cast(list[Event], mock_events), next_page_id=None
        )
        mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        # Use a timezone-aware timestamp (UTC)
        test_timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=True,
                after_timestamp=test_timestamp,
            )

        # search_events should be called with the normalized timestamp
        # (converted to server local timezone AND made naive for comparison)
        mock_event_service.search_events.assert_called_once()
        call_args = mock_event_service.search_events.call_args
        passed_timestamp = call_args.kwargs["timestamp__gte"]
        # The timestamp should be naive (no tzinfo)
        assert passed_timestamp is not None
        assert passed_timestamp.tzinfo is None
        # It should represent the same instant in time (converted to local)
        expected = test_timestamp.astimezone(None).replace(tzinfo=None)
        assert passed_timestamp == expected

    @pytest.mark.asyncio
    async def test_after_timestamp_without_resend_all_no_effect(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that after_timestamp has no effect when resend_all is False."""
        from datetime import datetime

        mock_websocket.receive_json.side_effect = WebSocketDisconnect()
        test_timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=False,
                after_timestamp=test_timestamp,
            )

        # search_events should not be called when resend_all is False
        mock_event_service.search_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_resend_all_without_after_timestamp(
        self, mock_websocket, mock_event_service, sample_conversation_id
    ):
        """Test that resend_all without after_timestamp passes None."""
        from typing import cast

        from openhands.agent_server.models import EventPage
        from openhands.sdk.event import Event

        mock_events = [
            MessageEvent(
                id="event1",
                source="user",
                llm_message=Message(role="user", content=[TextContent(text="Hello")]),
            ),
        ]
        mock_event_page = EventPage(
            items=cast(list[Event], mock_events), next_page_id=None
        )
        mock_event_service.search_events = AsyncMock(return_value=mock_event_page)
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()

        with (
            patch(
                "openhands.agent_server.sockets.conversation_service"
            ) as mock_conv_service,
            patch("openhands.agent_server.sockets.get_default_config") as mock_config,
        ):
            mock_config.return_value.session_api_keys = None
            mock_conv_service.get_event_service = AsyncMock(
                return_value=mock_event_service
            )

            from openhands.agent_server.sockets import events_socket

            await events_socket(
                sample_conversation_id,
                mock_websocket,
                session_api_key=None,
                resend_all=True,
                after_timestamp=None,
            )

        # search_events should be called with timestamp__gte=None
        mock_event_service.search_events.assert_called_once_with(
            page_id=None, timestamp__gte=None
        )


class TestAfterTimestampFilteringBehavioral:
    """Behavioral tests that verify actual filtering works with real events.

    These tests use real EventService instances with mock conversations
    to verify that timestamp filtering actually produces correct results,
    not just that the right methods were called.
    """

    @pytest.fixture
    def event_service_with_timestamped_events(self):
        """Create a real EventService with timestamped events for testing."""
        from pathlib import Path

        from openhands.agent_server.event_service import EventService
        from openhands.agent_server.models import StoredConversation
        from openhands.sdk import LLM, Agent
        from openhands.sdk.security.confirmation_policy import NeverConfirm
        from openhands.sdk.workspace import LocalWorkspace

        stored = StoredConversation(
            id=uuid4(),
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
        )
        service = EventService(
            stored=stored, conversations_dir=Path("test_conversation_dir")
        )

        # Create mock conversation with timestamped events
        from unittest.mock import MagicMock

        from openhands.sdk import Conversation
        from openhands.sdk.conversation.state import ConversationState

        conversation = MagicMock(spec=Conversation)
        state = MagicMock(spec=ConversationState)

        # Events with specific timestamps spanning 10:00 to 14:00
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
                llm_message=Message(
                    role="user", content=[TextContent(text=f"Message {index}")]
                ),
                timestamp=timestamp,
            )
            events.append(event)

        state.events = events
        state.__enter__ = MagicMock(return_value=state)
        state.__exit__ = MagicMock(return_value=None)
        conversation._state = state

        service._conversation = conversation
        return service

    @pytest.mark.asyncio
    async def test_timestamp_filter_returns_correct_events(
        self, event_service_with_timestamped_events
    ):
        """Test that timestamp filtering returns only events >= the filter time."""
        from datetime import datetime

        service = event_service_with_timestamped_events

        # Filter for events >= 12:00:00 (should return events 3, 4, 5)
        filter_time = datetime(2025, 1, 1, 12, 0, 0)
        result = await service.search_events(timestamp__gte=filter_time)

        # Should return exactly 3 events
        assert len(result.items) == 3

        # Verify the correct events were returned
        returned_ids = [event.id for event in result.items]
        assert "event3" in returned_ids
        assert "event4" in returned_ids
        assert "event5" in returned_ids

        # Events 1 and 2 should NOT be returned
        assert "event1" not in returned_ids
        assert "event2" not in returned_ids

    @pytest.mark.asyncio
    async def test_timestamp_filter_boundary_condition(
        self, event_service_with_timestamped_events
    ):
        """Test that filter boundary is inclusive (>=)."""
        from datetime import datetime

        service = event_service_with_timestamped_events

        # Filter for events >= exactly 12:00:00 (event3's timestamp)
        filter_time = datetime(2025, 1, 1, 12, 0, 0)
        result = await service.search_events(timestamp__gte=filter_time)

        # Event3 at exactly 12:00:00 should be included
        returned_ids = [event.id for event in result.items]
        assert "event3" in returned_ids

    @pytest.mark.asyncio
    async def test_timestamp_filter_no_matches(
        self, event_service_with_timestamped_events
    ):
        """Test that filter returns empty when no events match."""
        from datetime import datetime

        service = event_service_with_timestamped_events

        # Filter for events >= 15:00:00 (no events exist after 14:00)
        filter_time = datetime(2025, 1, 1, 15, 0, 0)
        result = await service.search_events(timestamp__gte=filter_time)

        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_timestamp_filter_all_events_match(
        self, event_service_with_timestamped_events
    ):
        """Test that all events are returned when filter is before all events."""
        from datetime import datetime

        service = event_service_with_timestamped_events

        # Filter for events >= 09:00:00 (before all events)
        filter_time = datetime(2025, 1, 1, 9, 0, 0)
        result = await service.search_events(timestamp__gte=filter_time)

        # Should return all 5 events
        assert len(result.items) == 5

    @pytest.mark.asyncio
    async def test_count_events_with_timestamp_filter(
        self, event_service_with_timestamped_events
    ):
        """Test that count_events also respects timestamp filtering."""
        from datetime import datetime

        service = event_service_with_timestamped_events

        # Count events >= 12:00:00 (should be 3)
        filter_time = datetime(2025, 1, 1, 12, 0, 0)
        count = await service.count_events(timestamp__gte=filter_time)

        assert count == 3
