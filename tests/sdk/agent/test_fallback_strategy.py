import pytest

from openhands.sdk.agent.base import FallbackStrategy
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


# ---------------------------------------------------------------------------
# get() with error=None
# ---------------------------------------------------------------------------


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
