"""Tests for SDK-3: cache rate derived properties on ``TokenUsage``."""

import math

import pytest

from openhands.sdk.llm.utils.metrics import TokenUsage


def test_cache_hit_rate_none_when_no_prompt_tokens():
    assert TokenUsage().cache_hit_rate is None
    assert TokenUsage(prompt_tokens=0, cache_read_tokens=100).cache_hit_rate is None


def test_cache_hit_rate_zero_when_no_cache_reads():
    u = TokenUsage(prompt_tokens=1000, cache_read_tokens=0)
    assert u.cache_hit_rate == 0.0


def test_cache_hit_rate_partial():
    u = TokenUsage(prompt_tokens=1000, cache_read_tokens=750)
    assert math.isclose(u.cache_hit_rate, 0.75)


def test_cache_hit_rate_clamped_to_one():
    """Some providers double-count cache reads inside prompt_tokens and
    others don't. Defensively clamp to 1.0 so dashboards never see >100%."""
    u = TokenUsage(prompt_tokens=1000, cache_read_tokens=1500)
    assert u.cache_hit_rate == 1.0


def test_cache_write_rate_basic():
    u = TokenUsage(prompt_tokens=1000, cache_write_tokens=200)
    assert math.isclose(u.cache_write_rate, 0.2)


def test_cache_write_rate_none_when_no_prompt_tokens():
    assert TokenUsage().cache_write_rate is None


def test_rates_survive_addition():
    """Hit rate is recomputed from accumulated totals after add."""
    a = TokenUsage(prompt_tokens=500, cache_read_tokens=400)
    b = TokenUsage(prompt_tokens=500, cache_read_tokens=100)
    total = a + b
    assert total.prompt_tokens == 1000
    assert total.cache_read_tokens == 500
    assert math.isclose(total.cache_hit_rate, 0.5)


@pytest.mark.parametrize(
    "prompt,read,expected",
    [
        (1, 0, 0.0),
        (1, 1, 1.0),
        (10, 5, 0.5),
    ],
)
def test_cache_hit_rate_parametric(prompt, read, expected):
    u = TokenUsage(prompt_tokens=prompt, cache_read_tokens=read)
    assert math.isclose(u.cache_hit_rate, expected)
