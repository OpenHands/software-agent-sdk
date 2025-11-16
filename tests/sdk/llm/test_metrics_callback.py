"""Tests for Metrics callback mechanism."""

from openhands.sdk.llm.utils.metrics import Metrics


def test_metrics_change_callback_on_add_cost():
    """Test that callback is triggered when cost is added."""
    metrics = Metrics(model_name="gpt-4")
    callback_calls = []

    def callback():
        callback_calls.append(True)

    metrics.set_on_change(callback)

    # Add cost - should trigger callback
    metrics.add_cost(0.05)

    assert len(callback_calls) == 1


def test_metrics_change_callback_on_add_token_usage():
    """Test that callback is triggered when token usage is added."""
    metrics = Metrics(model_name="gpt-4")
    callback_calls = []

    def callback():
        callback_calls.append(True)

    metrics.set_on_change(callback)

    # Add token usage - should trigger callback
    metrics.add_token_usage(
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=8000,
        response_id="resp1",
    )

    assert len(callback_calls) == 1


def test_metrics_change_callback_on_multiple_updates():
    """Test that callback is triggered for multiple updates."""
    metrics = Metrics(model_name="gpt-4")
    callback_calls = []

    def callback():
        callback_calls.append(True)

    metrics.set_on_change(callback)

    # Make multiple updates
    metrics.add_cost(0.05)
    metrics.add_token_usage(
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=8000,
        response_id="resp1",
    )
    metrics.add_cost(0.02)

    assert len(callback_calls) == 3


def test_metrics_callback_can_be_cleared():
    """Test that callback can be removed by setting to None."""
    metrics = Metrics(model_name="gpt-4")
    callback_calls = []

    def callback():
        callback_calls.append(True)

    # Set and then clear the callback
    metrics.set_on_change(callback)
    metrics.set_on_change(None)

    # Add cost - callback should not be called
    metrics.add_cost(0.05)

    assert len(callback_calls) == 0


def test_metrics_callback_exception_does_not_break_update():
    """Test that exceptions in callback don't prevent metrics updates."""

    def bad_callback():
        raise ValueError("Callback error")

    metrics = Metrics(model_name="gpt-4")
    metrics.set_on_change(bad_callback)

    # Add cost - should not raise despite callback error
    metrics.add_cost(0.05)

    # Verify metric was still updated
    assert metrics.accumulated_cost == 0.05


def test_metrics_merge_triggers_callback():
    """Test that merge operation triggers callback."""
    metrics1 = Metrics(model_name="gpt-4")
    metrics2 = Metrics(model_name="gpt-4")
    callback_calls = []

    def callback():
        callback_calls.append(True)

    metrics1.set_on_change(callback)

    # Add some costs to metrics2
    metrics2.add_cost(0.03)

    # Clear callback calls from previous operations
    callback_calls.clear()

    # Merge - should trigger callback
    metrics1.merge(metrics2)

    assert len(callback_calls) == 1


def test_metrics_add_response_latency_triggers_callback():
    """Test that adding response latency triggers callback."""
    metrics = Metrics(model_name="gpt-4")
    callback_calls = []

    def callback():
        callback_calls.append(True)

    metrics.set_on_change(callback)

    # Add response latency - should trigger callback
    metrics.add_response_latency(1.5, "resp1")

    assert len(callback_calls) == 1
