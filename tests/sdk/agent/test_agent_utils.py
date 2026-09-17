"""Tests for agent message preparation utilities."""

from unittest.mock import Mock, patch

import pytest

from openhands.sdk.agent.utils import prepare_llm_messages
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.event import Condensation, MessageEvent
from openhands.sdk.llm import Message, TextContent


# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_events():
    """Create sample events for testing."""
    return [
        MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="Hello, how can I help?")],
            ),
        ),
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="I need help with a task")],
            ),
        ),
        MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="I'll help you with that task")],
            ),
        ),
    ]


@pytest.fixture
def sample_messages():
    """Create sample messages for testing."""
    return [
        Message(
            role="user",
            content=[TextContent(text="Hello, how can I help?")],
        ),
        Message(
            role="assistant",
            content=[TextContent(text="I need help with a task")],
        ),
        Message(
            role="user",
            content=[TextContent(text="I'll help you with that task")],
        ),
    ]


@pytest.fixture
def mock_condenser():
    """Create a mock condenser for testing."""
    return Mock(spec=CondenserBase)


# ---------------------------------------------------------------------------
# Tests for prepare_llm_messages
# ---------------------------------------------------------------------------


@patch("openhands.sdk.event.base.LLMConvertibleEvent.events_to_messages")
def test_prepare_llm_messages_without_condenser(
    mock_events_to_messages, sample_events, sample_messages
):
    """Test prepare_llm_messages without condenser."""
    mock_events_to_messages.return_value = sample_messages
    view = View(events=sample_events)

    result = prepare_llm_messages(view)

    assert result == sample_messages
    mock_events_to_messages.assert_called_once_with(view.events)


@patch("openhands.sdk.event.base.LLMConvertibleEvent.events_to_messages")
def test_prepare_llm_messages_with_additional_messages(
    mock_events_to_messages, sample_events, sample_messages
):
    """Test prepare_llm_messages with additional messages."""
    # Copy to avoid mutation issues with the extend() inside prepare_llm_messages.
    mock_events_to_messages.return_value = sample_messages.copy()
    view = View(events=sample_events)

    additional_messages = [
        Message(
            role="user",
            content=[TextContent(text="Additional question")],
        )
    ]

    result = prepare_llm_messages(view, additional_messages=additional_messages)

    assert result == sample_messages + additional_messages
    mock_events_to_messages.assert_called_once_with(view.events)


@patch("openhands.sdk.event.base.LLMConvertibleEvent.events_to_messages")
def test_prepare_llm_messages_with_condenser_returns_view(
    mock_events_to_messages,
    sample_events,
    sample_messages,
    mock_condenser,
):
    """Test prepare_llm_messages with condenser that returns a View."""
    view = View(events=sample_events)

    condensed_view = View(events=sample_events[:2])
    mock_condenser.condense.return_value = condensed_view

    condensed_messages = sample_messages[:2]
    mock_events_to_messages.return_value = condensed_messages

    result = prepare_llm_messages(view, condenser=mock_condenser)

    assert result == condensed_messages
    mock_condenser.condense.assert_called_once_with(view, agent_llm=None)
    mock_events_to_messages.assert_called_once_with(condensed_view.events)


def test_prepare_llm_messages_with_condenser_returns_condensation(
    sample_events, mock_condenser
):
    """Test prepare_llm_messages with condenser that returns a Condensation."""
    view = View(events=sample_events)

    condensation = Condensation(
        summary="Test condensation summary",
        llm_response_id="test-response-id",
    )
    mock_condenser.condense.return_value = condensation

    result = prepare_llm_messages(view, condenser=mock_condenser)

    assert result == condensation
    mock_condenser.condense.assert_called_once_with(view, agent_llm=None)


@patch("openhands.sdk.event.base.LLMConvertibleEvent.events_to_messages")
def test_prepare_llm_messages_empty_view(mock_events_to_messages):
    """Test prepare_llm_messages with an empty view."""
    mock_events_to_messages.return_value = []
    view = View()

    result = prepare_llm_messages(view)

    assert result == []
    mock_events_to_messages.assert_called_once_with([])


def test_prepare_llm_messages_does_not_rebuild_view(monkeypatch, sample_events) -> None:
    """The hot path must consume the passed view directly without rebuilding it.

    This is the core perf invariant from #3053: the cached view on
    `ConversationState` is what eliminates the per-step `O(n)`
    `enforce_properties` cost, so `prepare_llm_messages` must not call
    `View.from_events` (which would run that enforcement again).
    """
    from_events_calls = 0
    enforce_calls = 0
    original_from_events = View.from_events
    original_enforce = View.enforce_properties

    def counting_from_events(events):
        nonlocal from_events_calls
        from_events_calls += 1
        return original_from_events(events)

    def counting_enforce(self, all_events):
        nonlocal enforce_calls
        enforce_calls += 1
        return original_enforce(self, all_events)

    monkeypatch.setattr(View, "from_events", staticmethod(counting_from_events))
    monkeypatch.setattr(View, "enforce_properties", counting_enforce)

    view = View(events=sample_events)
    prepare_llm_messages(view)

    assert from_events_calls == 0, (
        "prepare_llm_messages must not call View.from_events on the hot path"
    )
    assert enforce_calls == 0, (
        "prepare_llm_messages must not call enforce_properties on the hot path"
    )
