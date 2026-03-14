"""Test that ConversationStateUpdateEvent for stats uses MetricsSnapshot."""

import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def state():
    """Create a ConversationState for testing."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm)
    workspace = LocalWorkspace(working_dir="/tmp/test")

    state = ConversationState(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir="/tmp/test/.state",
        agent=agent,
    )

    # Set up filestore and enable autosave so callbacks are triggered
    state._fs = InMemoryFileStore()
    state._autosave_enabled = True

    return state


def test_stats_update_event_uses_snapshot_not_full_metrics(state):
    """Test that stats update event contains snapshot without lengthy lists."""
    callback_calls = []

    def callback(event: ConversationStateUpdateEvent):
        callback_calls.append(event)

    # Set the callback
    state.set_on_state_change(callback)

    # Create stats with multiple cost entries
    stats = ConversationStats()
    metrics = Metrics(model_name="gpt-4")

    # Add multiple cost entries to simulate a long conversation
    for i in range(10):
        metrics.add_cost(0.01)
        metrics.add_token_usage(
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=8000,
            response_id=f"resp{i}",
        )
        metrics.add_response_latency(1.5, f"resp{i}")

    stats.usage_to_metrics["default"] = metrics

    # Change state - should trigger callback
    with state:
        state.stats = stats

    # Verify callback was called
    assert len(callback_calls) == 1
    event = callback_calls[0]
    assert isinstance(event, ConversationStateUpdateEvent)
    assert event.key == "stats"

    # The event value should be a dict (already serialized as snapshot)
    stats_value = event.value
    assert isinstance(stats_value, dict)

    # Verify that stats_dict has the structure we expect
    assert "usage_to_metrics" in stats_value
    assert "default" in stats_value["usage_to_metrics"]

    metrics_data = stats_value["usage_to_metrics"]["default"]

    # After the fix, these lists should NOT be present
    # They grow with conversation length and cause bloat
    assert "costs" not in metrics_data, "costs list should not be present"
    assert "response_latencies" not in metrics_data, (
        "response_latencies list should not be present"
    )
    assert "token_usages" not in metrics_data, "token_usages list should not be present"

    # These should always be present (the snapshot data)
    assert "accumulated_cost" in metrics_data
    assert metrics_data["accumulated_cost"] == pytest.approx(0.1)
    assert "accumulated_token_usage" in metrics_data
    assert metrics_data["accumulated_token_usage"]["prompt_tokens"] == 1000
    assert metrics_data["accumulated_token_usage"]["completion_tokens"] == 500


def test_stats_model_dump_preserves_full_history():
    """Test that model_dump() preserves full metrics history for persistence."""
    # Create stats with multiple cost entries
    stats = ConversationStats()
    metrics = Metrics(model_name="gpt-4")

    # Add multiple entries to simulate a conversation
    for i in range(5):
        metrics.add_cost(0.01)
        metrics.add_token_usage(
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=8000,
            response_id=f"resp{i}",
        )
        metrics.add_response_latency(1.5, f"resp{i}")

    stats.usage_to_metrics["default"] = metrics

    # Use model_dump() without context - should preserve full history
    stats_dict = stats.model_dump(mode="json")

    assert "usage_to_metrics" in stats_dict
    assert "default" in stats_dict["usage_to_metrics"]

    metrics_data = stats_dict["usage_to_metrics"]["default"]

    # Full dump should contain all the lists
    assert "costs" in metrics_data, "costs list should be present in full dump"
    assert "response_latencies" in metrics_data, (
        "response_latencies list should be present in full dump"
    )
    assert "token_usages" in metrics_data, (
        "token_usages list should be present in full dump"
    )

    # Verify the lists have the correct number of entries
    assert len(metrics_data["costs"]) == 5
    assert len(metrics_data["response_latencies"]) == 5
    assert len(metrics_data["token_usages"]) == 5

    # Verify accumulated values are also present
    assert "accumulated_cost" in metrics_data
    assert metrics_data["accumulated_cost"] == pytest.approx(0.05)


def test_stats_model_dump_with_snapshot_context_excludes_history():
    """Test that model_dump() with use_snapshot context excludes lengthy lists."""
    # Create stats with multiple cost entries
    stats = ConversationStats()
    metrics = Metrics(model_name="gpt-4")

    # Add multiple entries to simulate a conversation
    for i in range(5):
        metrics.add_cost(0.01)
        metrics.add_token_usage(
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=8000,
            response_id=f"resp{i}",
        )
        metrics.add_response_latency(1.5, f"resp{i}")

    stats.usage_to_metrics["default"] = metrics

    # Use model_dump() with snapshot context - should exclude lists
    stats_dict = stats.model_dump(mode="json", context={"use_snapshot": True})

    assert "usage_to_metrics" in stats_dict
    assert "default" in stats_dict["usage_to_metrics"]

    metrics_data = stats_dict["usage_to_metrics"]["default"]

    # Snapshot should NOT contain the lists
    assert "costs" not in metrics_data, "costs list should not be in snapshot"
    assert "response_latencies" not in metrics_data, (
        "response_latencies list should not be in snapshot"
    )
    assert "token_usages" not in metrics_data, (
        "token_usages list should not be in snapshot"
    )

    # Verify accumulated values are present
    assert "accumulated_cost" in metrics_data
    assert metrics_data["accumulated_cost"] == pytest.approx(0.05)
    assert "accumulated_token_usage" in metrics_data
    assert metrics_data["accumulated_token_usage"]["prompt_tokens"] == 500
    assert metrics_data["accumulated_token_usage"]["completion_tokens"] == 250
