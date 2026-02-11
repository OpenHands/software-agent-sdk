import pytest

from openhands.sdk.agent.base import FallbackStrategy
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_construction():
    """FallbackStrategy with no arguments has empty defaults."""
    strategy = FallbackStrategy()
    assert strategy.default_fallbacks == []
    assert strategy.fallback_mapping == {}


def test_construction_with_default_fallbacks():
    strategy = FallbackStrategy(default_fallbacks=["fb-1", "fb-2"])
    assert strategy.default_fallbacks == ["fb-1", "fb-2"]
    assert strategy.fallback_mapping == {}


def test_construction_with_fallback_mapping():
    mapping: dict[type[Exception], list[str]] = {LLMRateLimitError: ["rate-limit-fb"]}
    strategy = FallbackStrategy(fallback_mapping=mapping)
    assert strategy.default_fallbacks == []
    assert strategy.fallback_mapping == mapping


def test_construction_with_both():
    mapping: dict[type[Exception], list[str]] = {LLMRateLimitError: ["rate-limit-fb"]}
    strategy = FallbackStrategy(
        default_fallbacks=["default-fb"],
        fallback_mapping=mapping,
    )
    assert strategy.default_fallbacks == ["default-fb"]
    assert strategy.fallback_mapping == mapping


# ---------------------------------------------------------------------------
# get() with error=None
# ---------------------------------------------------------------------------


def test_get_none_returns_default_fallbacks():
    strategy = FallbackStrategy(default_fallbacks=["fb-1"])
    assert strategy.resolve(None) == ["fb-1"]


def test_get_none_returns_empty_when_no_defaults():
    strategy = FallbackStrategy()
    assert strategy.resolve(None) == []


def test_get_no_arg_returns_default_fallbacks():
    """Calling get() without arguments returns default_fallbacks."""
    strategy = FallbackStrategy(default_fallbacks=["fb-1"])
    assert strategy.resolve() == ["fb-1"]


# ---------------------------------------------------------------------------
# get() with matching exceptions
# ---------------------------------------------------------------------------


def test_get_exact_match():
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["rate-limit-fb"]})
    assert strategy.resolve(LLMRateLimitError()) == ["rate-limit-fb"]


def test_get_subclass_does_not_match_parent_mapping():
    """Exact type lookup: subclass errors do not match a parent-class mapping."""
    strategy = FallbackStrategy(fallback_mapping={LLMError: ["generic-fb"]})
    # LLMRateLimitError is a subclass of LLMError, but resolve() uses exact-type lookup
    assert strategy.resolve(LLMRateLimitError()) == []


def test_get_first_matching_entry_wins():
    """When multiple entries match, the first in iteration order wins."""
    strategy = FallbackStrategy(
        fallback_mapping={
            LLMRateLimitError: ["specific-fb"],
            LLMError: ["generic-fb"],
        }
    )
    assert strategy.resolve(LLMRateLimitError()) == ["specific-fb"]


def test_get_multiple_fallbacks_in_mapping():
    strategy = FallbackStrategy(
        fallback_mapping={LLMTimeoutError: ["fb-1", "fb-2", "fb-3"]}
    )
    assert strategy.resolve(LLMTimeoutError()) == ["fb-1", "fb-2", "fb-3"]


# ---------------------------------------------------------------------------
# get() with non-matching exceptions
# ---------------------------------------------------------------------------


def test_get_non_matching_returns_empty():
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["rate-limit-fb"]})
    assert strategy.resolve(LLMAuthenticationError()) == []


def test_get_unrelated_exception_returns_empty():
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: ["rate-limit-fb"]})
    assert strategy.resolve(ValueError("unrelated")) == []


def test_get_empty_mapping_returns_empty():
    strategy = FallbackStrategy()
    assert strategy.resolve(LLMRateLimitError()) == []


def test_get_mapping_to_empty_list():
    """Mapping an exception to an empty list yields no fallbacks."""
    strategy = FallbackStrategy(fallback_mapping={LLMRateLimitError: []})
    assert strategy.resolve(LLMRateLimitError()) == []


# ---------------------------------------------------------------------------
# get() with both defaults and mapping
# ---------------------------------------------------------------------------


def test_get_matching_prefers_mapping_over_defaults():
    """When the error matches a mapping, the mapping takes precedence."""
    strategy = FallbackStrategy(
        default_fallbacks=["default-fb"],
        fallback_mapping={LLMRateLimitError: ["rate-limit-fb"]},
    )
    assert strategy.resolve(LLMRateLimitError()) == ["rate-limit-fb"]


def test_get_non_matching_falls_back_to_defaults():
    """Non-matching errors return default_fallbacks."""
    strategy = FallbackStrategy(
        default_fallbacks=["default-fb"],
        fallback_mapping={LLMRateLimitError: ["rate-limit-fb"]},
    )
    assert strategy.resolve(LLMAuthenticationError()) == ["default-fb"]


# ---------------------------------------------------------------------------
# Multiple exception types in mapping
# ---------------------------------------------------------------------------


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
