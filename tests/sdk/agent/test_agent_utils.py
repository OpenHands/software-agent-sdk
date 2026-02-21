"""Tests for agent utility functions.

This module tests the prepare_llm_messages and make_llm_completion utility
functions that are used by the agent for message preparation and LLM calls.
"""

from unittest.mock import Mock

import pytest
from pydantic import Field

from openhands.sdk.agent.utils import make_llm_completion, prepare_llm_messages
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.event import Condensation, MessageEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.tool import Action, Observation, ToolDefinition


# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm():
    """Create a mock LLM for testing."""
    llm = Mock(spec=LLM)
    llm.uses_responses_api.return_value = False
    return llm


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


class MockAgentUtilsAction(Action):
    """Mock action for agent utils testing."""

    param1: str = Field(description="First parameter")


class MockAgentUtilsObservation(Observation):
    """Mock observation for agent utils testing."""

    result: str = Field(description="Result of the action")

    @property
    def to_llm_content(self):
        return [TextContent(text=self.result)]


class MockAgentUtilsTool(
    ToolDefinition[MockAgentUtilsAction, MockAgentUtilsObservation]
):
    """Mock tool definition for agent utils testing."""

    @classmethod
    def create(cls, conv_state=None, **params):
        return [cls(**params)]


@pytest.fixture
def sample_tools():
    """Create sample tool definitions for testing."""
    return [
        MockAgentUtilsTool(
            description="A test tool for agent utils",
            action_type=MockAgentUtilsAction,
            observation_type=MockAgentUtilsObservation,
        )
    ]


# ---------------------------------------------------------------------------
# Tests for prepare_llm_messages
# ---------------------------------------------------------------------------


def test_prepare_llm_messages_without_condenser(sample_events):
    """Test prepare_llm_messages without condenser."""
    view = View(events=sample_events)

    result = prepare_llm_messages(view)

    assert len(result) == len(sample_events)


def test_prepare_llm_messages_with_additional_messages(sample_events):
    """Test prepare_llm_messages with additional messages."""
    view = View(events=sample_events)
    additional_messages = [
        Message(
            role="user",
            content=[TextContent(text="Additional question")],
        )
    ]

    result = prepare_llm_messages(view, additional_messages=additional_messages)

    # The result should have messages from the view events + the additional one
    assert len(result) == len(sample_events) + 1
    assert result[-1] == additional_messages[0]


def test_prepare_llm_messages_with_condenser_returns_view(
    sample_events, mock_condenser
):
    """Test prepare_llm_messages with condenser that returns a View."""
    view = View(events=sample_events)

    condensed_view = View(events=sample_events[:2])
    mock_condenser.condense.return_value = condensed_view

    result = prepare_llm_messages(view, condenser=mock_condenser)

    assert isinstance(result, list)
    assert len(result) == 2
    mock_condenser.condense.assert_called_once_with(view, agent_llm=None)


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


def test_prepare_llm_messages_empty_view():
    """Test prepare_llm_messages with empty view."""
    view = View()

    result = prepare_llm_messages(view)

    assert result == []


# ---------------------------------------------------------------------------
# Tests for make_llm_completion
# ---------------------------------------------------------------------------


def test_make_llm_completion_with_completion_api(mock_llm, sample_messages):
    """Test make_llm_completion using completion API."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, sample_messages)

    # Verify results
    assert result == mock_response
    mock_llm.uses_responses_api.assert_called_once()
    mock_llm.completion.assert_called_once_with(
        messages=sample_messages,
        tools=[],
        add_security_risk_prediction=True,
        on_token=None,
    )
    mock_llm.responses.assert_not_called()


def test_make_llm_completion_with_responses_api(mock_llm, sample_messages):
    """Test make_llm_completion using responses API."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = True
    mock_response = Mock(spec=LLMResponse)
    mock_llm.responses.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, sample_messages)

    # Verify results
    assert result == mock_response
    mock_llm.uses_responses_api.assert_called_once()
    mock_llm.responses.assert_called_once_with(
        messages=sample_messages,
        tools=[],
        include=None,
        store=False,
        add_security_risk_prediction=True,
        on_token=None,
    )
    mock_llm.completion.assert_not_called()


def test_make_llm_completion_with_tools_completion_api(
    mock_llm, sample_messages, sample_tools
):
    """Test make_llm_completion with tools using completion API."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, sample_messages, tools=sample_tools)

    # Verify results
    assert result == mock_response
    mock_llm.uses_responses_api.assert_called_once()
    mock_llm.completion.assert_called_once_with(
        messages=sample_messages,
        tools=sample_tools,
        add_security_risk_prediction=True,
        on_token=None,
    )


def test_make_llm_completion_with_tools_responses_api(
    mock_llm, sample_messages, sample_tools
):
    """Test make_llm_completion with tools using responses API."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = True
    mock_response = Mock(spec=LLMResponse)
    mock_llm.responses.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, sample_messages, tools=sample_tools)

    # Verify results
    assert result == mock_response
    mock_llm.uses_responses_api.assert_called_once()
    mock_llm.responses.assert_called_once_with(
        messages=sample_messages,
        tools=sample_tools,
        include=None,
        store=False,
        add_security_risk_prediction=True,
        on_token=None,
    )


def test_make_llm_completion_with_none_tools(mock_llm, sample_messages):
    """Test make_llm_completion with None tools parameter."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, sample_messages, tools=None)

    # Verify results
    assert result == mock_response
    mock_llm.completion.assert_called_once_with(
        messages=sample_messages,
        tools=[],
        add_security_risk_prediction=True,
        on_token=None,
    )


def test_make_llm_completion_with_empty_tools_list(mock_llm, sample_messages):
    """Test make_llm_completion with empty tools list."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, sample_messages, tools=[])

    # Verify results
    assert result == mock_response
    mock_llm.completion.assert_called_once_with(
        messages=sample_messages,
        tools=[],
        add_security_risk_prediction=True,
        on_token=None,
    )


def test_make_llm_completion_empty_messages(mock_llm):
    """Test make_llm_completion with empty messages list."""
    # Setup mock
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    # Call function
    result = make_llm_completion(mock_llm, [])

    # Verify results
    assert result == mock_response
    mock_llm.completion.assert_called_once_with(
        messages=[],
        tools=[],
        add_security_risk_prediction=True,
        on_token=None,
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_prepare_llm_messages_and_make_llm_completion_integration(
    sample_events, mock_llm
):
    """Test integration between prepare_llm_messages and make_llm_completion."""
    view = View(events=sample_events)

    # Setup mocks for make_llm_completion
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    # Call functions in sequence (simulating real usage)
    messages = prepare_llm_messages(view)
    result = make_llm_completion(mock_llm, messages)

    assert len(messages) == len(sample_events)
    assert result == mock_response
    mock_llm.completion.assert_called_once_with(
        messages=messages,
        tools=[],
        add_security_risk_prediction=True,
        on_token=None,
    )


def test_make_llm_completion_api_selection():
    """Test that make_llm_completion correctly selects between completion and responses APIs."""  # noqa: E501
    # Test completion API selection
    mock_llm = Mock(spec=LLM)
    mock_llm.uses_responses_api.return_value = False
    mock_response = Mock(spec=LLMResponse)
    mock_llm.completion.return_value = mock_response

    messages = [
        Message(
            role="user",
            content=[TextContent(text="Hello, test message")],
        )
    ]

    result = make_llm_completion(mock_llm, messages)

    assert result == mock_response
    mock_llm.uses_responses_api.assert_called_once()
    mock_llm.completion.assert_called_once_with(
        messages=messages,
        tools=[],
        add_security_risk_prediction=True,
        on_token=None,
    )
    mock_llm.responses.assert_not_called()

    # Reset mocks and test responses API selection
    mock_llm.reset_mock()
    mock_llm.uses_responses_api.return_value = True
    mock_llm.responses.return_value = mock_response

    result = make_llm_completion(mock_llm, messages)

    assert result == mock_response
    mock_llm.uses_responses_api.assert_called_once()
    mock_llm.responses.assert_called_once_with(
        messages=messages,
        tools=[],
        include=None,
        store=False,
        add_security_risk_prediction=True,
        on_token=None,
    )
    mock_llm.completion.assert_not_called()
