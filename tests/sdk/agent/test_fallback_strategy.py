from unittest.mock import Mock

import pytest

from openhands.sdk.agent.base import FallbackStrategy
from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


@pytest.mark.parametrize(
    "fallback_mapping, default_fallbacks",
    [
        ({}, []),
        ({}, ["fb-1", "fb-2"]),
        ({LLMRateLimitError: ["rate-limit-fb"]}, []),
        ({LLMRateLimitError: ["rate-limit-fb"]}, ["fb-1", "fb-2"]),
    ],
)
def test_default_construction(
    fallback_mapping: dict[type[Exception], list[str]],
    default_fallbacks: list[str],
) -> None:
    """FallbackStrategy with no arguments has empty defaults."""
    strategy = FallbackStrategy(
        fallback_mapping=fallback_mapping,
        default_fallbacks=default_fallbacks,
    )
    assert strategy.default_fallbacks == default_fallbacks
    assert strategy.fallback_mapping == fallback_mapping


@pytest.mark.parametrize(
    "fallback_mapping, default_fallbacks, resolve_input, expected_output",
    [
        ({}, [], None, []),
        ({}, ["fb-1"], None, ["fb-1"]),
        (
            {LLMRateLimitError: ["rate-limit-fb"]},
            [],
            LLMRateLimitError,
            ["rate-limit-fb"],
        ),
        ({LLMError: ["generic-fb"]}, [], LLMRateLimitError, []),
        (
            {LLMError: ["generic-fb"]},
            ["another-profile"],
            LLMRateLimitError,
            ["another-profile"],
        ),
        (
            {LLMTimeoutError: ["fb-1", "fb-2", "fb-3"]},
            [],
            LLMTimeoutError,
            ["fb-1", "fb-2", "fb-3"],
        ),
        (
            {LLMRateLimitError: ["rate-limit-fb"]},
            ["default-fb"],
            LLMRateLimitError,
            ["rate-limit-fb"],
        ),
        (
            {LLMRateLimitError: ["rate-limit-fb"]},
            ["default-fb"],
            LLMRateLimitError,
            ["default-fb"],
        ),
    ],
)
def test_get_none_returns_default_fallbacks(
    fallback_mapping: dict[type[Exception], list[str]],
    default_fallbacks: list[str],
    resolve_input: Exception | None,
    expected_output: list[str],
) -> None:
    strategy = FallbackStrategy(default_fallbacks=default_fallbacks)
    assert strategy.resolve(resolve_input) == default_fallbacks


@pytest.mark.parametrize(
    "exc_class,expected",
    [
        (LLMRateLimitError, ["rate-limit-fb"]),
        (LLMTimeoutError, ["timeout-fb"]),
        (LLMServiceUnavailableError, ["unavailable-fb"]),
    ],
)
def test_get_selects_correct_mapping(exc_class, expected):
    strategy = FallbackStrategy(
        fallback_mapping={
            LLMRateLimitError: ["rate-limit-fb"],
            LLMTimeoutError: ["timeout-fb"],
            LLMServiceUnavailableError: ["unavailable-fb"],
        }
    )
    assert strategy.resolve(exc_class()) == expected


# ---------------------------------------------------------------------------
# _on_llm_created callback and caching
# ---------------------------------------------------------------------------


def test_on_llm_created_fires_for_new_llm():
    """When get_fallback_llms loads an LLM, the _on_llm_created callback fires."""
    mock_store = Mock()
    mock_llm = Mock(spec=LLM)
    mock_store.load.return_value = mock_llm

    callback = Mock()

    strategy = FallbackStrategy(
        fallback_mapping={LLMRateLimitError: ["fb-1"]},
    )
    strategy.__dict__["profile_store"] = mock_store
    strategy.set_on_llm_created(callback)

    results = list(strategy.get_fallback_llms(LLMRateLimitError()))

    assert len(results) == 1
    assert results[0] == ("fb-1", mock_llm)
    callback.assert_called_once_with(mock_llm)


def test_cached_llm_does_not_refire_callback():
    """Second call for same profile uses cache and does NOT re-fire callback."""
    mock_store = Mock()
    mock_llm = Mock(spec=LLM)
    mock_store.load.return_value = mock_llm

    callback = Mock()

    strategy = FallbackStrategy(
        fallback_mapping={LLMRateLimitError: ["fb-1"]},
    )
    strategy.__dict__["profile_store"] = mock_store
    strategy.set_on_llm_created(callback)

    # First call – loads and fires callback
    list(strategy.get_fallback_llms(LLMRateLimitError()))
    # Second call – should use cache
    results = list(strategy.get_fallback_llms(LLMRateLimitError()))

    assert len(results) == 1
    assert results[0] == ("fb-1", mock_llm)
    # profile_store.load called only once (first call)
    mock_store.load.assert_called_once_with("fb-1")
    # callback also fired only once
    callback.assert_called_once_with(mock_llm)


def test_no_callback_set_still_loads_and_caches():
    """Without a callback, get_fallback_llms still loads and caches LLMs."""
    mock_store = Mock()
    mock_llm = Mock(spec=LLM)
    mock_store.load.return_value = mock_llm

    strategy = FallbackStrategy(
        fallback_mapping={LLMRateLimitError: ["fb-1"]},
    )
    strategy.__dict__["profile_store"] = mock_store

    results = list(strategy.get_fallback_llms(LLMRateLimitError()))
    assert len(results) == 1
    assert results[0] == ("fb-1", mock_llm)

    # Second call uses cache
    list(strategy.get_fallback_llms(LLMRateLimitError()))
    mock_store.load.assert_called_once_with("fb-1")


def test_callback_fires_per_distinct_profile():
    """Each distinct profile triggers the callback exactly once."""
    mock_store = Mock()
    llm_a = Mock(spec=LLM)
    llm_b = Mock(spec=LLM)
    mock_store.load.side_effect = [llm_a, llm_b]

    callback = Mock()

    strategy = FallbackStrategy(
        fallback_mapping={LLMRateLimitError: ["fb-a", "fb-b"]},
    )
    strategy.__dict__["profile_store"] = mock_store
    strategy.set_on_llm_created(callback)

    results = list(strategy.get_fallback_llms(LLMRateLimitError()))

    assert len(results) == 2
    assert callback.call_count == 2
    callback.assert_any_call(llm_a)
    callback.assert_any_call(llm_b)
