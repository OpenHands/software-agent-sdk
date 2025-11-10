"""Tests for RemoteEventsList."""

from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest

from openhands.sdk.conversation.impl.remote_conversation import RemoteEventsList
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, TextContent


@pytest.fixture
def mock_client():
    """Create mock HTTP client."""
    return Mock(spec=httpx.Client)


@pytest.fixture
def conversation_id():
    """Test conversation ID."""
    return "test-conv-id"


def create_mock_event(event_id: str) -> Event:
    """Create a test event."""
    return MessageEvent(
        id=event_id,
        timestamp=datetime.now().isoformat(),
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text=f"Message {event_id}")]
        ),
    )


def create_mock_api_response(events: list[Event], next_page_id: str | None = None):
    """Create a mock API response."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "items": [event.model_dump() for event in events],
        "next_page_id": next_page_id,
    }
    return mock_response


def test_remote_events_list_single_page(mock_client, conversation_id):
    """Test loading events from a single page."""
    events = [
        create_mock_event("event-1"),
        create_mock_event("event-2"),
        create_mock_event("event-3"),
    ]

    mock_response = create_mock_api_response(events)
    mock_client.request.return_value = mock_response

    events_list = RemoteEventsList(mock_client, conversation_id)

    assert isinstance(events_list, RemoteEventsList)
    assert len(events_list) == 3
    assert events_list[0].id == "event-1"
    assert events_list[2].id == "event-3"


def test_remote_events_list_pagination(mock_client, conversation_id):
    """Test loading events across multiple pages."""
    page1_events = [create_mock_event("event-1"), create_mock_event("event-2")]
    page2_events = [create_mock_event("event-3"), create_mock_event("event-4")]

    page1_response = create_mock_api_response(page1_events, "page-2")
    page2_response = create_mock_api_response(page2_events)

    mock_client.request.side_effect = [page1_response, page2_response]

    events_list = RemoteEventsList(mock_client, conversation_id)

    assert len(events_list) == 4
    assert events_list[0].id == "event-1"
    assert events_list[3].id == "event-4"
    assert mock_client.request.call_count == 2


def test_remote_events_list_indexing_and_slicing(mock_client, conversation_id):
    """Test list-like indexing and slicing operations."""
    events = [
        create_mock_event("event-1"),
        create_mock_event("event-2"),
        create_mock_event("event-3"),
    ]

    mock_response = create_mock_api_response(events)
    mock_client.request.return_value = mock_response

    events_list = RemoteEventsList(mock_client, conversation_id)

    # Positive and negative indexing
    assert events_list[0].id == "event-1"
    assert events_list[-1].id == "event-3"

    # Slicing
    slice_result = events_list[1:3]
    assert len(slice_result) == 2
    assert slice_result[0].id == "event-2"

    # Iteration
    assert [e.id for e in events_list] == ["event-1", "event-2", "event-3"]


def test_remote_events_list_add_event_deduplication(mock_client, conversation_id):
    """Test adding events with automatic deduplication."""
    mock_response = create_mock_api_response([])
    mock_client.request.return_value = mock_response

    events_list = RemoteEventsList(mock_client, conversation_id)

    event = create_mock_event("new-event")
    events_list.add_event(event)
    assert len(events_list) == 1

    # Adding duplicate should be ignored
    events_list.add_event(event)
    assert len(events_list) == 1

    # Adding event with same ID should be ignored
    duplicate = create_mock_event("new-event")
    events_list.add_event(duplicate)
    assert len(events_list) == 1
    assert events_list[0] != duplicate
    assert events_list[0] == event


def test_remote_events_list_callback_integration(mock_client, conversation_id):
    """Test callback integration for event streaming."""
    mock_response = create_mock_api_response([])
    mock_client.request.return_value = mock_response

    events_list = RemoteEventsList(mock_client, conversation_id)
    callback = events_list.create_default_callback()

    test_event = create_mock_event("callback-event")
    callback(test_event)

    # Default callback should add event to the list
    assert len(events_list) == 1
    assert events_list[0].id == "callback-event"


def test_remote_events_list_api_error(mock_client, conversation_id):
    """Test error propagation when API calls fail."""
    mock_request = Mock()
    mock_error_response = Mock()
    mock_error_response.status_code = 500

    mock_response = Mock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "API Error", request=mock_request, response=mock_error_response
    )
    mock_client.request.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        RemoteEventsList(mock_client, conversation_id)


def test_remote_events_list_empty(mock_client, conversation_id):
    """Test handling of empty event lists."""
    mock_response = create_mock_api_response([])
    mock_client.request.return_value = mock_response

    events_list = RemoteEventsList(mock_client, conversation_id)

    assert len(events_list) == 0
    assert list(events_list) == []

    with pytest.raises(IndexError):
        _ = events_list[0]
