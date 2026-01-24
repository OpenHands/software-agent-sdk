from unittest.mock import MagicMock

import pytest

from openhands.sdk.conversation import (
    TokenDisplayMode,
    compute_token_display,
)
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.llm.utils.metrics import Metrics


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # Ensure env vars do not leak between tests
    monkeypatch.delenv("OH_TOKENS_VIEW_MODE", raising=False)
    monkeypatch.delenv("OH_TOKENS_VIEW_DELTA", raising=False)


def _make_stats_with_two_requests():
    stats = ConversationStats()
    m = Metrics(model_name="test-model")
    # First call
    m.add_cost(0.1)
    m.add_token_usage(
        prompt_tokens=100,
        completion_tokens=25,
        cache_read_tokens=10,
        cache_write_tokens=0,
        reasoning_tokens=5,
        context_window=8000,
        response_id="first",
    )
    # Second call
    m.add_cost(0.05)
    m.add_token_usage(
        prompt_tokens=220,
        completion_tokens=80,
        cache_read_tokens=44,
        cache_write_tokens=0,
        reasoning_tokens=0,
        context_window=8000,
        response_id="second",
    )
    stats.usage_to_metrics["usage-1"] = m
    return stats


def test_compute_token_display_per_context_with_delta():
    stats = _make_stats_with_two_requests()

    td = compute_token_display(
        stats=stats, mode=TokenDisplayMode.PER_CONTEXT, include_since_last=True
    )
    assert td is not None

    # Latest request values
    assert td.input_tokens == 220
    assert td.output_tokens == 80
    assert td.reasoning_tokens == 0
    assert td.cache_read_tokens == 44
    assert td.context_window == 8000
    assert td.total_cost == pytest.approx(0.15)
    assert td.cache_hit_rate == pytest.approx(44 / 220)

    # Delta vs previous
    assert td.since_last_input_tokens == 120  # 220 - 100


def test_compute_token_display_accumulated():
    stats = _make_stats_with_two_requests()

    td = compute_token_display(stats=stats, mode=TokenDisplayMode.ACCUMULATED)
    assert td is not None

    # Accumulated values: sums of prompt/completion/cache_read; max context_window
    assert td.input_tokens == 100 + 220
    assert td.output_tokens == 25 + 80
    assert td.cache_read_tokens == 10 + 44
    assert td.reasoning_tokens == 5 + 0
    assert td.context_window == 8000
    assert td.total_cost == pytest.approx(0.15)
    assert td.cache_hit_rate == pytest.approx((10 + 44) / (100 + 220))

    # No since-last in accumulated mode
    assert td.since_last_input_tokens is None


def test_visualizer_env_vars_toggle_delta(monkeypatch):
    stats = _make_stats_with_two_requests()

    # Force per-context and delta
    monkeypatch.setenv("OH_TOKENS_VIEW_MODE", "per_context")
    monkeypatch.setenv("OH_TOKENS_VIEW_DELTA", "true")

    viz = DefaultConversationVisualizer()

    # Attach stats via the base visualizer's initialize contract using a simple mock
    state = MagicMock()
    state.stats = stats
    viz.initialize(state)
    subtitle = viz._format_metrics_subtitle()
    assert subtitle is not None
    assert "(+" in subtitle  # shows since-last delta

    # Force accumulated mode: should hide delta even if env says true
    monkeypatch.setenv("OH_TOKENS_VIEW_MODE", "accumulated")
    subtitle2 = viz._format_metrics_subtitle()
    assert subtitle2 is not None
    assert "(+" not in subtitle2
