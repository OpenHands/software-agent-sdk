from unittest.mock import Mock, patch

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import FallbackStrategy
from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMContextWindowExceedError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


MESSAGES = [Message(role="user", content=[TextContent(text="Hello")])]
TOOLS: list = []


def _make_agent(
    fallback_strategy: FallbackStrategy | None = None,
    mock_profile_store: Mock | None = None,
) -> Agent:
    llm = LLM(model="test-model", usage_id="test-primary")
    strategy = fallback_strategy or FallbackStrategy()
    if mock_profile_store is not None:
        # Inject mock into the cached_property slot so the real
        # LLMProfileStore is never instantiated.
        strategy.__dict__["profile_store"] = mock_profile_store
    return Agent(
        llm=llm,
        tools=[],
        llm_fallback_strategy=strategy,
    )


# ---------------------------------------------------------------------------
# Primary succeeds
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_primary_succeeds(mock_make_completion):
    """When primary LLM succeeds, return its response without trying fallbacks."""
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.return_value = mock_response

    agent = _make_agent()
    result = agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    assert result is mock_response
    mock_make_completion.assert_called_once()


# ---------------------------------------------------------------------------
# Primary fails, no fallbacks configured
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_no_fallbacks_raises_original(mock_make_completion):
    """With no fallback strategy, the original exception propagates."""
    mock_make_completion.side_effect = LLMRateLimitError("rate limited")

    agent = _make_agent()

    with pytest.raises(LLMRateLimitError, match="rate limited"):
        agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_empty_fallback_list_raises(mock_make_completion):
    """A mapping to an empty list is treated as no fallbacks available."""
    mock_make_completion.side_effect = LLMRateLimitError("rate limited")

    mock_store = Mock()
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: []})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    with pytest.raises(LLMRateLimitError, match="rate limited"):
        agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    mock_store.load.assert_not_called()


# ---------------------------------------------------------------------------
# Primary fails, fallback succeeds
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_fallback_succeeds(mock_make_completion):
    """When primary fails and the error is mapped, try the fallback LLM."""
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.side_effect = [LLMRateLimitError(), mock_response]

    mock_store = Mock()
    mock_store.load.return_value = Mock(spec=LLM)
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["my-fallback"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    result = agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    assert result is mock_response
    mock_store.load.assert_called_once_with("my-fallback")
    assert mock_make_completion.call_count == 2


# ---------------------------------------------------------------------------
# Multiple fallbacks: first fails, second succeeds
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_second_fallback_succeeds(mock_make_completion):
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.side_effect = [
        LLMServiceUnavailableError(),
        LLMTimeoutError(),
        mock_response,
    ]
    mock_store = Mock()
    mock_fb1 = Mock(spec=LLM)
    mock_fb2 = Mock(spec=LLM)
    mock_store.load.side_effect = [mock_fb1, mock_fb2]

    strategy = FallbackStrategy(
        fallback_mapping={LLMServiceUnavailableError: ["fb-1", "fb-2"]}
    )
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    result = agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    assert result is mock_response
    assert mock_make_completion.call_count == 3
    assert mock_store.load.call_count == 2


# ---------------------------------------------------------------------------
# Non-mapped exception raises immediately
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_unmapped_exception_raises_immediately(mock_make_completion):
    """Exceptions not in the fallback mapping are re-raised without trying fallbacks."""
    mock_make_completion.side_effect = LLMAuthenticationError("bad key")

    mock_store = Mock()
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["fb-1"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    with pytest.raises(LLMAuthenticationError, match="bad key"):
        agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    mock_store.load.assert_not_called()


@pytest.mark.parametrize(
    "exc_class",
    [LLMAuthenticationError, LLMContextWindowExceedError],
)
@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_non_transient_exceptions_skip_fallback(mock_make_completion, exc_class):
    mock_make_completion.side_effect = exc_class()

    mock_store = Mock()
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["fb-1"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    with pytest.raises(exc_class):
        agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    mock_store.load.assert_not_called()


# ---------------------------------------------------------------------------
# Parametrize: mapped transient exceptions trigger fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_class",
    [LLMServiceUnavailableError, LLMRateLimitError, LLMTimeoutError],
)
@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_mapped_exceptions_trigger_fallback(mock_make_completion, exc_class):
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.side_effect = [exc_class(), mock_response]

    mock_store = Mock()
    mock_store.load.return_value = Mock(spec=LLM)
    strategy = FallbackStrategy(fallback_mapping={exc_class: ["fb-1"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    result = agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    assert result is mock_response
    mock_store.load.assert_called_once_with("fb-1")


# ---------------------------------------------------------------------------
# Profile load failure is treated as fallback failure
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_profile_load_failure_skips_to_next(mock_make_completion):
    """If LLMProfileStore.load fails, the next fallback in the list is tried."""
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.side_effect = [
        LLMRateLimitError(),
        mock_response,  # only called for fb-2 (fb-1 load fails)
    ]
    mock_store = Mock()
    mock_store.load.side_effect = [
        FileNotFoundError("profile not found"),
        Mock(spec=LLM),
    ]

    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["fb-1", "fb-2"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    result = agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)

    assert result is mock_response
    assert mock_store.load.call_count == 2


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_all_profiles_fail_to_load(mock_make_completion):
    """If all profile loads fail, the last load error is raised."""
    mock_make_completion.side_effect = LLMRateLimitError()
    mock_store = Mock()
    mock_store.load.side_effect = FileNotFoundError("no profile")

    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["fb-1"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    with pytest.raises(Exception):
        agent._make_llm_completion_with_fallback(MESSAGES, TOOLS)


# ---------------------------------------------------------------------------
# on_token is forwarded to fallback completions
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_on_token_forwarded_to_fallback(mock_make_completion):
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.side_effect = [LLMRateLimitError(), mock_response]

    mock_store = Mock()
    mock_store.load.return_value = Mock(spec=LLM)
    on_token = Mock()
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["fb-1"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    agent._make_llm_completion_with_fallback(MESSAGES, TOOLS, on_token=on_token)

    # Both primary and fallback calls should receive on_token
    for call in mock_make_completion.call_args_list:
        assert call.kwargs["on_token"] is on_token


# ---------------------------------------------------------------------------
# tools are forwarded to fallback completions
# ---------------------------------------------------------------------------


@patch("openhands.sdk.agent.agent.make_llm_completion")
def test_tools_forwarded_to_fallback(mock_make_completion):
    mock_response = Mock(spec=LLMResponse)
    mock_make_completion.side_effect = [LLMRateLimitError(), mock_response]

    mock_store = Mock()
    mock_store.load.return_value = Mock(spec=LLM)
    tools = [Mock(), Mock()]
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["fb-1"]})
    agent = _make_agent(fallback_strategy=strategy, mock_profile_store=mock_store)

    agent._make_llm_completion_with_fallback(MESSAGES, tools)

    # Both primary and fallback calls should receive the same tools list
    for call in mock_make_completion.call_args_list:
        assert call.kwargs["tools"] is tools
