"""Tests for P1: ``ConversationStats.cache_efficiency_summary()``."""

from openhands.sdk import ConversationStats
from openhands.sdk.llm.utils.metrics import Metrics, TokenUsage


def _stats_with(*usages: tuple[str, str, TokenUsage]) -> ConversationStats:
    """Build a ConversationStats containing one Metrics per (usage_id, model, usage)."""
    stats = ConversationStats()
    for usage_id, model, usage in usages:
        m = Metrics(model_name=model)
        m.accumulated_token_usage = usage
        stats.usage_to_metrics[usage_id] = m
    return stats


def test_empty_stats_returns_none_rates():
    summary = ConversationStats().cache_efficiency_summary()
    assert summary["prompt_tokens"] == 0
    assert summary["cache_read_tokens"] == 0
    assert summary["cache_hit_rate"] is None
    assert summary["cache_write_rate"] is None
    assert summary["per_usage"] == {}


def test_metric_without_completions_yet_yields_none_aggregate():
    """A registered LLM that hasn't recorded any completions yet shows up
    in ``per_usage`` with zero tokens, but the aggregate hit rate stays
    ``None`` so dashboards distinguish 'no data' from '0% hit'."""
    stats = ConversationStats()
    stats.usage_to_metrics["empty"] = Metrics(model_name="claude-sonnet-4-5")
    summary = stats.cache_efficiency_summary()
    assert summary["cache_hit_rate"] is None
    assert summary["per_usage"]["empty"]["prompt_tokens"] == 0
    assert summary["per_usage"]["empty"]["cache_hit_rate"] is None


def test_single_llm_summary():
    stats = _stats_with(
        (
            "main",
            "claude-sonnet-4-5",
            TokenUsage(
                model="claude-sonnet-4-5",
                prompt_tokens=10000,
                completion_tokens=200,
                cache_read_tokens=8000,
                cache_write_tokens=500,
            ),
        )
    )
    s = stats.cache_efficiency_summary()
    assert s["prompt_tokens"] == 10000
    assert s["cache_read_tokens"] == 8000
    assert s["completion_tokens"] == 200
    assert s["cache_hit_rate"] == 0.8
    assert s["cache_write_rate"] == 0.05
    assert s["per_usage"]["main"]["model"] == "claude-sonnet-4-5"
    assert s["per_usage"]["main"]["cache_hit_rate"] == 0.8


def test_two_llms_aggregate_correctly():
    """Aggregate hit rate is weighted by prompt tokens, not a naive mean of
    per-LLM rates. With 9k cached out of 11k total prompt tokens, the
    aggregate is 9/11 ≈ 0.818, *not* the mean of 0.9 and 0.0."""
    stats = _stats_with(
        (
            "cached-llm",
            "claude-sonnet-4-5",
            TokenUsage(prompt_tokens=10000, cache_read_tokens=9000),
        ),
        (
            "uncached-llm",
            "nemotron-3-ultra-550b",
            TokenUsage(prompt_tokens=1000, cache_read_tokens=0),
        ),
    )
    s = stats.cache_efficiency_summary()
    assert s["prompt_tokens"] == 11000
    assert s["cache_read_tokens"] == 9000
    assert abs(s["cache_hit_rate"] - 9000 / 11000) < 1e-9
    # per-LLM breakdown stays unweighted and surfaces the bad citizen
    assert s["per_usage"]["uncached-llm"]["cache_hit_rate"] == 0.0
    assert s["per_usage"]["cached-llm"]["cache_hit_rate"] == 0.9


def test_aggregate_clamped_when_provider_double_counts():
    """Some providers report cache_read_tokens *outside* prompt_tokens.
    The summary must clamp to 1.0 so dashboards don't see >100%."""
    stats = _stats_with(
        (
            "weird",
            "weird-model",
            TokenUsage(prompt_tokens=1000, cache_read_tokens=1500),
        ),
    )
    s = stats.cache_efficiency_summary()
    assert s["cache_hit_rate"] == 1.0


def test_summary_falls_back_to_metrics_model_name():
    """If TokenUsage.model is empty, fall back to the Metrics' model_name."""
    m = Metrics(model_name="some-model")
    m.accumulated_token_usage = TokenUsage(prompt_tokens=100, cache_read_tokens=50)
    stats = ConversationStats()
    stats.usage_to_metrics["u"] = m
    s = stats.cache_efficiency_summary()
    assert s["per_usage"]["u"]["model"] == "some-model"
