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
    llm = LLM(model="gpt-4", api_key=SecretStr("test-key"), usage_id="test-llm")
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

    # The event value is ConversationStats object
    # When it gets serialized (e.g., to send over websocket),
    # it will be converted to JSON
    stats_value = event.value
    assert isinstance(stats_value, ConversationStats)

    # Serialize it to see what gets sent over the wire
    stats_dict = stats_value.model_dump(mode="json")

    # Verify that stats_dict has the structure we expect
    assert "usage_to_metrics" in stats_dict
    assert "default" in stats_dict["usage_to_metrics"]

    metrics_data = stats_dict["usage_to_metrics"]["default"]

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
