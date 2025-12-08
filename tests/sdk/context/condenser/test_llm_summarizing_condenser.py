from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse

from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation
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
    assert result.summary_offset == keep_first
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
    events = [message_event(f"Event {i}") for i in range(max_size + 1)]

    # Add a condensation to simulate previous summarization
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

    # The prompt should contain the previous summary
    assert "Previous summary content" in prompt_text


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
    _, kwargs = completion_mock.call_args
    assert "extra_body" not in kwargs
