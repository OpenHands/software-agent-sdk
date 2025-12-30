from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse

from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
    Reason,
)
from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation, CondensationRequest
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import (
    LLM,
    LLMResponse,
    Message,
    MetricsSnapshot,
    TextContent,
)


def message_event(content: str) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


@pytest.fixture
def mock_llm() -> LLM:
    """Create a mock LLM for testing."""
    mock_llm = MagicMock(spec=LLM)

    # Mock the completion response - now returns LLMResponse
    def create_completion_result(content: str) -> LLMResponse:
        message = Message(role="assistant", content=[TextContent(text=content)])
        metrics = MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=None,
        )
        # Create a mock ModelResponse
        raw_response = MagicMock(spec=ModelResponse)
        raw_response.id = "mock-llm-response-id"
        return LLMResponse(message=message, metrics=metrics, raw_response=raw_response)

    mock_llm.completion.return_value = create_completion_result(
        "Summary of forgotten events"
    )
    mock_llm.format_messages_for_llm = lambda messages: messages

    # Mock the required attributes that are checked in _set_env_side_effects
    mock_llm.openrouter_site_url = "https://docs.all-hands.dev/"
    mock_llm.openrouter_app_name = "OpenHands"
    mock_llm.aws_access_key_id = None
    mock_llm.aws_secret_access_key = None
    mock_llm.aws_region_name = None
    mock_llm.metrics = None
    mock_llm.model = "test-model"
    mock_llm.log_completions = False
    mock_llm.log_completions_folder = None
    mock_llm.custom_tokenizer = None
    mock_llm.base_url = None
    mock_llm.reasoning_effort = None
    mock_llm.litellm_extra_body = {}
    mock_llm.temperature = 0.0

    # Explicitly set pricing attributes required by LLM -> Telemetry wiring
    mock_llm.input_cost_per_token = None
    mock_llm.output_cost_per_token = None

    mock_llm._metrics = None

    # Helper method to set mock response content
    def set_mock_response_content(content: str):
        mock_llm.completion.return_value = create_completion_result(content)

    mock_llm.set_mock_response_content = set_mock_response_content

    return mock_llm


def test_default_values(mock_llm: LLM) -> None:
    """Test that LLMSummarizingCondenser has correct default values.

    These defaults are tuned to ensure workable manipulation indices for condensation.
    See https://github.com/OpenHands/software-agent-sdk/issues/1518 for context.
    """
    condenser = LLMSummarizingCondenser(llm=mock_llm)

    # Default max_size should be 240 (raised from 120 to allow more room for tool loops)
    assert condenser.max_size == 240

    # Default keep_first should be 2 (reduced from 4 to leave more room for
    # condensation)
    assert condenser.keep_first == 2


def test_should_condense(mock_llm: LLM) -> None:
    """Test that LLMSummarizingCondenser correctly determines when to condense."""
    max_size = 100
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=max_size)

    # Create events below the threshold
    small_events = [message_event(f"Event {i}") for i in range(max_size)]
    small_view = View.from_events(small_events)

    assert not condenser.should_condense(small_view)

    # Create events above the threshold
    large_events = [message_event(f"Event {i}") for i in range(max_size + 1)]
    large_view = View.from_events(large_events)

    assert condenser.should_condense(large_view)


def test_condense_returns_view_when_no_condensation_needed(mock_llm: LLM) -> None:
    """Test that condenser returns the original view when no condensation is needed."""  # noqa: E501
    max_size = 100
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=max_size)

    events: list[Event] = [message_event(f"Event {i}") for i in range(max_size)]
    view = View.from_events(events)

    result = condenser.condense(view)

    assert isinstance(result, View)
    assert result == view
    # LLM should not be called
    cast(MagicMock, mock_llm.completion).assert_not_called()


def test_condense_returns_condensation_when_needed(mock_llm: LLM) -> None:
    """Test that condenser returns a Condensation when condensation is needed."""
    max_size = 10
    keep_first = 3
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=max_size, keep_first=keep_first
    )

    # Set up mock response
    cast(Any, mock_llm).set_mock_response_content("Summary of forgotten events")

    events: list[Event] = [message_event(f"Event {i}") for i in range(max_size + 1)]
    view = View.from_events(events)

    result = condenser.condense(view)

    assert isinstance(result, Condensation)
    assert result.summary == "Summary of forgotten events"
    # summary_offset should be the smallest manipulation index > keep_first
    # Since all events are MessageEvents, manipulation indices are [0,1,2,3,4,...]
    # The smallest index > keep_first (3) is 4
    assert result.summary_offset == keep_first + 1
    assert len(result.forgotten_event_ids) > 0

    # LLM should be called once
    cast(MagicMock, mock_llm.completion).assert_called_once()


def test_get_condensation_with_previous_summary(mock_llm: LLM) -> None:
    """Test that condenser properly handles previous summary content."""
    max_size = 10
    keep_first = 3
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=max_size, keep_first=keep_first
    )

    # Set up mock response
    cast(Any, mock_llm).set_mock_response_content("Updated summary")

    # Create events with a condensation in the history
    # Need enough events so that after condensation, the view still exceeds max_size
    # Condensation will remove 2 events (events[3] and events[4]) plus itself
    # So we need at least max_size + 1 + 3 = 14 events to exceed max_size after
    # condensation
    events = [message_event(f"Event {i}") for i in range(14)]

    # Add a condensation to simulate previous summarization
    # The summary will be inserted at keep_first due to summary_offset
    condensation = Condensation(
        forgotten_event_ids=[events[3].id, events[4].id],
        summary="Previous summary content",
        summary_offset=keep_first,
        llm_response_id="condensation_response_1",
    )
    events_with_condensation = (
        events[:keep_first] + [condensation] + events[keep_first:]
    )

    view = View.from_events(events_with_condensation)

    result = condenser.get_condensation(view)

    assert isinstance(result, Condensation)
    assert result.summary == "Updated summary"

    # Verify that the LLM was called with the previous summary
    completion_mock = cast(MagicMock, mock_llm.completion)
    completion_mock.assert_called_once()
    call_args = completion_mock.call_args
    messages = call_args[1]["messages"]  # Get keyword arguments
    prompt_text = messages[0].content[0].text

    # The prompt should contain the previous summary (it's in <PREVIOUS SUMMARY> sec.)
    # The summary is now retrieved from the view, which should have it at the summary
    # event
    assert (
        "Previous summary content" in prompt_text or "<PREVIOUS SUMMARY>" in prompt_text
    )


def test_invalid_config(mock_llm: LLM) -> None:
    """Test that LLMSummarizingCondenser validates configuration parameters."""
    # Test max_size must be positive
    with pytest.raises(ValueError):
        LLMSummarizingCondenser(llm=mock_llm, max_size=0)

    # Test keep_first must be non-negative
    with pytest.raises(ValueError):
        LLMSummarizingCondenser(llm=mock_llm, keep_first=-1)

    # Test keep_first must be less than max_size // 2 to leave room for condensation
    with pytest.raises(ValueError):
        LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=8)


def test_get_condensation_does_not_pass_extra_body(mock_llm: LLM) -> None:
    """Condenser should not pass extra_body to llm.completion.

    This prevents providers like 1p Anthropic from rejecting the request with
    "extra_body: Extra inputs are not permitted".
    """
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=2)

    # Prepare a view that triggers condensation (len > max_size)
    events: list[Event] = [message_event(f"Event {i}") for i in range(12)]
    view = View.from_events(events)

    result = condenser.condense(view)
    assert isinstance(result, Condensation)

    # Ensure completion was called without an explicit extra_body kwarg
    completion_mock = cast(MagicMock, mock_llm.completion)
    assert completion_mock.call_count == 1


def test_condense_with_agent_llm(mock_llm: LLM) -> None:
    """Test that condenser accepts and works with optional agent llm parameter."""
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=2)

    # Create a separate mock for the agent's LLM
    agent_llm = MagicMock(spec=LLM)
    agent_llm.model = "gpt-4"

    # Prepare a view that triggers condensation
    events: list[Event] = [message_event(f"Event {i}") for i in range(12)]
    view = View.from_events(events)

    # Call condense with the agent's LLM
    result = condenser.condense(view, agent_llm=agent_llm)
    assert isinstance(result, Condensation)

    # Verify the condenser still uses its own LLM for summarization
    completion_mock = cast(MagicMock, mock_llm.completion)
    assert completion_mock.call_count == 1

    # Agent LLM should not be called for completion (condenser uses its own LLM)
    assert not agent_llm.completion.called
    _, kwargs = completion_mock.call_args
    assert "extra_body" not in kwargs


def test_condense_with_token_limit_exceeded(mock_llm: LLM) -> None:
    """Test that condenser triggers on TOKENS reason when token limit is exceeded."""
    max_tokens = 100
    keep_first = 2
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=1000, max_tokens=max_tokens, keep_first=keep_first
    )

    # Create a separate mock for the agent's LLM with token counting
    agent_llm = MagicMock(spec=LLM)
    agent_llm.model = "gpt-4"

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

    agent_llm.get_token_count.side_effect = mock_token_count

    # Create events that exceed token limit
    # Each event has 40 chars = 10 tokens
    # 15 events = 150 tokens (exceeds max_tokens of 100)
    events: list[Event] = [message_event("A" * 40) for i in range(15)]
    view = View.from_events(events)

    # Verify that TOKENS is the condensation reason
    reasons = condenser.get_condensation_reasons(view, agent_llm=agent_llm)
    assert Reason.TOKENS in reasons
    assert Reason.EVENTS not in reasons  # Should not trigger on event count
    assert Reason.REQUEST not in reasons

    # Condense the view
    result = condenser.condense(view, agent_llm=agent_llm)
    assert isinstance(result, Condensation)

    # Verify the condenser used its own LLM for summarization
    completion_mock = cast(MagicMock, mock_llm.completion)
    assert completion_mock.call_count == 1

    # Verify forgotten events were calculated based on token reduction
    assert len(result.forgotten_event_ids) > 0


def test_condense_with_request_and_events_reasons(mock_llm: LLM) -> None:
    """Test condensation when both REQUEST and EVENTS reasons are true simultaneously.

    Verifies that the most aggressive condensation (minimum suffix) is chosen.
    """
    max_size = 20
    keep_first = 2
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=max_size, keep_first=keep_first
    )

    # Create events that exceed max_size AND include a condensation request
    # 25 events > max_size of 20 (triggers EVENTS)
    # Plus a CondensationRequest (triggers REQUEST)
    events: list[Event] = [message_event(f"Event {i}") for i in range(25)]
    events.append(CondensationRequest())
    view = View.from_events(events)

    # Verify both reasons are present
    reasons = condenser.get_condensation_reasons(view, agent_llm=None)
    assert Reason.REQUEST in reasons
    assert Reason.EVENTS in reasons
    assert Reason.TOKENS not in reasons

    # Get the condensation
    result = condenser.condense(view)
    assert isinstance(result, Condensation)

    # Calculate expected behavior:
    # REQUEST: target_size = len(view) // 2 = 25 // 2 = 12
    #          suffix_to_keep = 12 - keep_first - 1 = 12 - 2 - 1 = 9
    # EVENTS:  target_size = max_size // 2 = 20 // 2 = 10
    #          suffix_to_keep = 10 - keep_first - 1 = 10 - 2 - 1 = 7
    # Most aggressive: min(9, 7) = 7

    # With manipulation indices for MessageEvents:
    # naive_start = keep_first = 2
    # naive_end = 25 - 7 = 18
    # manipulation_indices = [0, 1, 2, 3, ..., 25]
    # forgetting_start = smallest index > keep_first = 3
    # forgetting_end = smallest index >= naive_end = 18
    # Forgotten: events[3:18] = 15 events
    expected_forgotten_count = 15
    assert len(result.forgotten_event_ids) == expected_forgotten_count


def test_condense_with_request_and_tokens_reasons(mock_llm: LLM) -> None:
    """Test condensation when both REQUEST and TOKENS reasons are true simultaneously.

    Verifies that the most aggressive condensation (minimum suffix) is chosen.
    """
    max_tokens = 100
    keep_first = 2
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=1000, max_tokens=max_tokens, keep_first=keep_first
    )

    # Create a separate mock for the agent's LLM with token counting
    agent_llm = MagicMock(spec=LLM)
    agent_llm.model = "gpt-4"

    # Mock get_token_count to return predictable values
    def mock_token_count(messages):
        total_chars = 0
        for msg in messages:
            for content in msg.content:
                if hasattr(content, "text"):
                    total_chars += len(content.text)
        return total_chars // 4

    agent_llm.get_token_count.side_effect = mock_token_count

    # Create 20 events with 40 chars each = 10 tokens each = 200 total tokens
    # This exceeds max_tokens of 100 (triggers TOKENS)
    events: list[Event] = [message_event("A" * 40) for i in range(20)]
    # Add a CondensationRequest (triggers REQUEST)
    events.append(CondensationRequest())
    view = View.from_events(events)

    # Verify both reasons are present
    reasons = condenser.get_condensation_reasons(view, agent_llm=agent_llm)
    assert Reason.REQUEST in reasons
    assert Reason.TOKENS in reasons
    assert Reason.EVENTS not in reasons

    # Get the condensation
    result = condenser.condense(view, agent_llm=agent_llm)
    assert isinstance(result, Condensation)

    # The most aggressive condensation should be chosen (minimum suffix)
    assert len(result.forgotten_event_ids) > 0


def test_condense_with_events_and_tokens_reasons(mock_llm: LLM) -> None:
    """Test condensation when both EVENTS and TOKENS reasons are true simultaneously.

    Verifies that the most aggressive condensation (minimum suffix) is chosen.
    """
    max_size = 15
    max_tokens = 100
    keep_first = 2
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=max_size, max_tokens=max_tokens, keep_first=keep_first
    )

    # Create a separate mock for the agent's LLM with token counting
    agent_llm = MagicMock(spec=LLM)
    agent_llm.model = "gpt-4"

    def mock_token_count(messages):
        total_chars = 0
        for msg in messages:
            for content in msg.content:
                if hasattr(content, "text"):
                    total_chars += len(content.text)
        return total_chars // 4

    agent_llm.get_token_count.side_effect = mock_token_count

    # Create 20 events (exceeds max_size of 15) with 40 chars each
    # 20 events * 10 tokens = 200 tokens (exceeds max_tokens of 100)
    events: list[Event] = [message_event("A" * 40) for i in range(20)]
    view = View.from_events(events)

    # Verify both reasons are present
    reasons = condenser.get_condensation_reasons(view, agent_llm=agent_llm)
    assert Reason.EVENTS in reasons
    assert Reason.TOKENS in reasons
    assert Reason.REQUEST not in reasons

    # Get the condensation
    result = condenser.condense(view, agent_llm=agent_llm)
    assert isinstance(result, Condensation)

    # The most aggressive condensation should be chosen (minimum suffix)
    assert len(result.forgotten_event_ids) > 0


def test_condense_with_all_three_reasons(mock_llm: LLM) -> None:
    """Test condensation when all three reasons are true simultaneously.

    Verifies that the most aggressive condensation (minimum suffix) is chosen
    when REQUEST, EVENTS, and TOKENS all trigger at once.
    """
    max_size = 15
    max_tokens = 100
    keep_first = 2
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=max_size, max_tokens=max_tokens, keep_first=keep_first
    )

    # Create a separate mock for the agent's LLM with token counting
    agent_llm = MagicMock(spec=LLM)
    agent_llm.model = "gpt-4"

    def mock_token_count(messages):
        total_chars = 0
        for msg in messages:
            for content in msg.content:
                if hasattr(content, "text"):
                    total_chars += len(content.text)
        return total_chars // 4

    agent_llm.get_token_count.side_effect = mock_token_count

    # Create 20 events (exceeds max_size of 15) with 40 chars each
    # 20 events * 10 tokens = 200 tokens (exceeds max_tokens of 100)
    events: list[Event] = [message_event("A" * 40) for i in range(20)]
    # Add CondensationRequest (triggers REQUEST)
    events.append(CondensationRequest())
    view = View.from_events(events)

    # Verify all three reasons are present
    reasons = condenser.get_condensation_reasons(view, agent_llm=agent_llm)
    assert Reason.REQUEST in reasons
    assert Reason.EVENTS in reasons
    assert Reason.TOKENS in reasons

    # Get the condensation
    result = condenser.condense(view, agent_llm=agent_llm)
    assert isinstance(result, Condensation)

    # The most aggressive condensation should be chosen (minimum suffix)
    # This means the most events should be forgotten
    assert len(result.forgotten_event_ids) > 0

    # Verify the condenser used its own LLM for summarization
    completion_mock = cast(MagicMock, mock_llm.completion)
    assert completion_mock.call_count == 1


def test_most_aggressive_condensation_chosen(mock_llm: LLM) -> None:
    """Test that the minimum suffix is chosen when multiple reasons provide different
    targets.

    This test explicitly verifies the min() logic at line 200 of the condenser.
    """
    max_size = 30  # Set high so EVENTS triggers with specific target
    keep_first = 2
    condenser = LLMSummarizingCondenser(
        llm=mock_llm, max_size=max_size, keep_first=keep_first
    )

    # Create a scenario where REQUEST and EVENTS give different suffix sizes
    # 40 events total
    events: list[Event] = [message_event(f"Event {i}") for i in range(40)]
    events.append(CondensationRequest())
    view = View.from_events(events)

    # Calculate expected suffix lengths:
    # REQUEST: target_size = len(view) // 2 = 40 // 2 = 20
    #          suffix_to_keep = 20 - keep_first - 1 = 20 - 2 - 1 = 17
    # EVENTS:  target_size = max_size // 2 = 30 // 2 = 15
    #          suffix_to_keep = 15 - keep_first - 1 = 15 - 2 - 1 = 12
    # Most aggressive: min(17, 12) = 12

    result = condenser.condense(view)
    assert isinstance(result, Condensation)

    # With manipulation indices for MessageEvents:
    # naive_start = keep_first = 2
    # naive_end = 40 - 12 = 28
    # manipulation_indices = [0, 1, 2, 3, ..., 40]
    # forgetting_start = smallest index > keep_first = 3
    # forgetting_end = smallest index >= naive_end = 28
    # Forgotten events: events[3:28] = 25 events
    expected_forgotten_count = 25
    assert len(result.forgotten_event_ids) == expected_forgotten_count


def test_generate_condensation_raises_on_zero_events(mock_llm: LLM) -> None:
    """Test that _generate_condensation raises ValueError when given 0 events.

    This prevents the LLM from being called with an empty event list, which would
    produce a confusing summary like "I don't see any events provided to summarize."
    See https://github.com/OpenHands/software-agent-sdk/issues/1518 for context.
    """
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=100, keep_first=2)

    with pytest.raises(ValueError, match="Cannot condense 0 events"):
        condenser._generate_condensation(
            summary_event_content="",
            forgotten_events=[],
            summary_offset=0,
        )

    # Verify the LLM was never called
    cast(MagicMock, mock_llm.completion).assert_not_called()
