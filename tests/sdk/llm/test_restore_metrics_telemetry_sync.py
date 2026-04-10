"""Additional tests for restore_metrics() telemetry sync (OpenHands/OpenHands#13843).

These tests complement test_issue_2459_restore_metrics_syncs_telemetry in
test_llm.py by covering edge cases: cost propagation, stale-metrics isolation,
telemetry=None safety, and end-to-end ConversationStats integration.
"""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, ConversationStats, RegistryEvent
from openhands.sdk.llm.utils.metrics import Metrics


@pytest.fixture
def llm():
    """Create a minimal SDK LLM for testing."""
    return LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-service",
    )


def test_cost_recorded_in_restored_metrics(llm):
    """Costs added via telemetry after restore must land in the restored Metrics."""
    restored = Metrics(model_name="openai/gpt-4o")
    restored.add_cost(5.00)
    llm.restore_metrics(restored)

    llm.telemetry.metrics.add_cost(0.50)

    assert llm.metrics.accumulated_cost == 5.50
    assert len(llm.metrics.costs) == 2


def test_stale_metrics_not_updated(llm):
    """The original (pre-restore) Metrics must not receive new costs."""
    original_metrics = llm.metrics

    restored = Metrics(model_name="openai/gpt-4o")
    restored.add_cost(2.00)
    llm.restore_metrics(restored)

    llm.telemetry.metrics.add_cost(0.75)

    assert original_metrics.accumulated_cost == 0.0
    assert llm.metrics.accumulated_cost == 2.75


def test_restore_metrics_telemetry_none():
    """restore_metrics() must not crash when telemetry has not been initialized."""
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-service",
    )
    llm._telemetry = None

    restored = Metrics(model_name="openai/gpt-4o")
    restored.add_cost(1.00)
    llm.restore_metrics(restored)

    assert llm.metrics is restored
    assert llm.metrics.accumulated_cost == 1.00


def test_conversation_stats_restore_then_track():
    """End-to-end: ConversationStats restores metrics, then new costs are tracked."""
    saved_metrics = Metrics(model_name="openai/gpt-4o")
    saved_metrics.add_cost(10.00)

    stats = ConversationStats(usage_to_metrics={"agent": saved_metrics})

    with patch("openhands.sdk.llm.llm.litellm_completion"):
        llm = LLM(
            model="openai/gpt-4o",
            api_key=SecretStr("test-key"),
            usage_id="agent",
        )
        event = RegistryEvent(llm=llm)
        stats.register_llm(event)

        assert llm.metrics.accumulated_cost == 10.00

        # Simulate a new LLM response adding cost via telemetry
        llm.telemetry.metrics.add_cost(0.25)

        assert llm.metrics.accumulated_cost == 10.25
        assert stats.get_combined_metrics().accumulated_cost == 10.25
