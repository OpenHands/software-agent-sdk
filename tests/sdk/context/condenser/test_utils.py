from unittest.mock import MagicMock

import pytest

from openhands.sdk.context.condenser.utils import (
    get_shortest_prefix_above_token_count,
    get_suffix_length_for_token_reduction,
    get_total_token_count,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent


def message_event(content: str) -> MessageEvent:
    """Helper function to create a MessageEvent for testing."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


@pytest.fixture
def mock_llm() -> LLM:
    """Create a mock LLM with token counting capability."""
    mock_llm = MagicMock(spec=LLM)
    mock_llm.model = "test-model"

    # Mock get_token_count to return predictable values based on message content length
    def mock_token_count(messages):
        # Simple heuristic: count characters in all text content
        # Each character = 0.25 tokens (roughly 4 chars per token)
        total_chars = 0
        for msg in messages:
            for content in msg.content:
                if hasattr(content, "text"):
                    total_chars += len(content.text)
        return total_chars // 4

    mock_llm.get_token_count.side_effect = mock_token_count

    return mock_llm


class TestGetTotalTokenCount:
    """Tests for get_total_token_count function."""

    def test_empty_events(self, mock_llm: LLM):
        """Test with empty event list."""
        events = []
        token_count = get_total_token_count(events, mock_llm)
        assert token_count == 0

    def test_single_event(self, mock_llm: LLM):
        """Test with a single event."""
        events = [message_event("Hello world")]  # 11 chars -> 2 tokens
        token_count = get_total_token_count(events, mock_llm)
        assert token_count == 2

    def test_multiple_events(self, mock_llm: LLM):
        """Test with multiple events."""
        events = [
            message_event("Hello"),  # 5 chars -> 1 token
            message_event("World"),  # 5 chars -> 1 token
            message_event("Test message"),  # 12 chars -> 3 tokens
        ]
        token_count = get_total_token_count(events, mock_llm)
        assert token_count == 5  # (5 + 5 + 12) // 4 = 5

    def test_events_converted_to_messages(self, mock_llm: LLM):
        """Test that events are properly converted to messages."""
        events = [message_event("Test")]
        get_total_token_count(events, mock_llm)

        # Verify get_token_count was called
        assert mock_llm.get_token_count.called  # type: ignore
        # Verify it was called with a list of messages
        call_args = mock_llm.get_token_count.call_args[0][0]  # type: ignore
        assert isinstance(call_args, list)
        assert all(isinstance(msg, Message) for msg in call_args)


class TestGetShortestPrefixAboveTokenCount:
    """Tests for get_shortest_prefix_above_token_count function."""

    def test_empty_events(self, mock_llm: LLM):
        """Test with empty event list."""
        events = []
        prefix_length = get_shortest_prefix_above_token_count(events, mock_llm, 10)
        assert prefix_length == 0

    def test_no_prefix_exceeds_token_count(self, mock_llm: LLM):
        """Test when total tokens don't exceed the target."""
        events = [
            message_event("Hi"),  # 2 chars -> 0 tokens
            message_event("Bye"),  # 3 chars -> 0 tokens
        ]
        prefix_length = get_shortest_prefix_above_token_count(events, mock_llm, 100)
        assert prefix_length == len(events)

    def test_single_event_exceeds(self, mock_llm: LLM):
        """Test when first event alone exceeds the token count."""
        events = [
            message_event("A" * 100),  # 100 chars -> 25 tokens
            message_event("B" * 100),  # 100 chars -> 25 tokens
        ]
        prefix_length = get_shortest_prefix_above_token_count(events, mock_llm, 20)
        assert prefix_length == 1

    def test_multiple_events_needed(self, mock_llm: LLM):
        """Test when multiple events are needed to exceed token count."""
        events = [
            message_event("A" * 20),  # 20 chars -> 5 tokens
            message_event("B" * 20),  # 20 chars -> 5 tokens
            message_event("C" * 20),  # 20 chars -> 5 tokens
            message_event("D" * 20),  # 20 chars -> 5 tokens
        ]
        # Need prefix of 3 events to exceed 10 tokens (15 > 10)
        prefix_length = get_shortest_prefix_above_token_count(events, mock_llm, 10)
        assert prefix_length == 3

    def test_exact_boundary(self, mock_llm: LLM):
        """Test behavior at exact token count boundary."""
        events = [
            message_event("A" * 40),  # 40 chars -> 10 tokens
            message_event("B" * 40),  # 40 chars -> 10 tokens
        ]
        # 10 tokens is not > 10, need 2 events for 20 tokens
        prefix_length = get_shortest_prefix_above_token_count(events, mock_llm, 10)
        assert prefix_length == 2

    def test_all_events_needed(self, mock_llm: LLM):
        """Test when all events together just exceed the token count."""
        events = [
            message_event("A" * 16),  # 16 chars -> 4 tokens
            message_event("B" * 16),  # 16 chars -> 4 tokens
            message_event("C" * 16),  # 16 chars -> 4 tokens
        ]
        # Total 12 tokens, need all 3 to exceed 10
        prefix_length = get_shortest_prefix_above_token_count(events, mock_llm, 10)
        assert prefix_length == 3


class TestGetSuffixLengthForTokenReduction:
    """Tests for get_suffix_length_for_token_reduction function."""

    def test_empty_events(self, mock_llm: LLM):
        """Test with empty event list."""
        events = []
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, 10)
        assert suffix_length == 0

    def test_zero_token_reduction(self, mock_llm: LLM):
        """Test with zero token reduction requested."""
        events = [
            message_event("Test"),
            message_event("Message"),
        ]
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, 0)
        assert suffix_length == len(events)

    def test_negative_token_reduction(self, mock_llm: LLM):
        """Test with negative token reduction (edge case)."""
        events = [
            message_event("Test"),
            message_event("Message"),
        ]
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, -10)
        assert suffix_length == len(events)

    def test_small_reduction(self, mock_llm: LLM):
        """Test with small token reduction that removes few events."""
        events = [
            message_event("A" * 40),  # 40 chars -> 10 tokens
            message_event("B" * 40),  # 40 chars -> 10 tokens
            message_event("C" * 40),  # 40 chars -> 10 tokens
            message_event("D" * 40),  # 40 chars -> 10 tokens
        ]
        # Total 40 tokens, reduce by 15 means keep suffix after removing 1 event (10
        # tokens). Actually need to remove 2 events (20 tokens) to exceed 15 token
        # reduction
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, 15)
        assert suffix_length == 2  # Keep last 2 events

    def test_large_reduction(self, mock_llm: LLM):
        """Test with large token reduction that removes most events."""
        events = [
            message_event("A" * 20),  # 20 chars -> 5 tokens
            message_event("B" * 20),  # 20 chars -> 5 tokens
            message_event("C" * 20),  # 20 chars -> 5 tokens
            message_event("D" * 20),  # 20 chars -> 5 tokens
        ]
        # Total 20 tokens, reduce by 18 tokens means remove 4 events (20 tokens)
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, 18)
        assert suffix_length == 0  # Keep nothing

    def test_exact_reduction(self, mock_llm: LLM):
        """Test with exact token reduction matching some events."""
        events = [
            message_event("A" * 40),  # 40 chars -> 10 tokens
            message_event("B" * 40),  # 40 chars -> 10 tokens
            message_event("C" * 40),  # 40 chars -> 10 tokens
        ]
        # Total 30 tokens, reduce by exactly 10 tokens
        # Need to remove 2 events (20 tokens) to exceed 10 token reduction
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, 10)
        assert suffix_length == 1  # Keep last 1 event

    def test_impossible_reduction(self, mock_llm: LLM):
        """Test when requested reduction exceeds total tokens."""
        events = [
            message_event("Hi"),  # 2 chars -> 0 tokens
            message_event("Bye"),  # 3 chars -> 0 tokens
        ]
        # Total ~0 tokens, but asking to reduce by 100
        suffix_length = get_suffix_length_for_token_reduction(events, mock_llm, 100)
        assert suffix_length == 0  # Can't keep anything

    def test_consistency_with_prefix_function(self, mock_llm: LLM):
        """Test that suffix calculation is consistent with prefix calculation."""
        events = [
            message_event("A" * 40),  # 40 chars -> 10 tokens
            message_event("B" * 40),  # 40 chars -> 10 tokens
            message_event("C" * 40),  # 40 chars -> 10 tokens
            message_event("D" * 40),  # 40 chars -> 10 tokens
        ]
        token_reduction = 25

        suffix_length = get_suffix_length_for_token_reduction(
            events, mock_llm, token_reduction
        )
        prefix_length = get_shortest_prefix_above_token_count(
            events, mock_llm, token_reduction
        )

        # Suffix + prefix should equal total length
        assert suffix_length + prefix_length == len(events)
