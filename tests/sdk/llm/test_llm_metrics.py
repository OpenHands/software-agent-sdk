"""Tests for LLM metrics classes."""

import pytest
from pydantic import ValidationError

from openhands.sdk.llm.utils.metrics import Cost, Metrics, ResponseLatency, TokenUsage


def test_cost_creation_valid():
    """Test creating a valid Cost instance."""
    cost = Cost(cost=5.0, model="gpt-4o-mini")
    assert cost.cost == 5.0
    assert cost.model == "gpt-4o-mini"
    assert hasattr(cost, "timestamp")


def test_cost_creation_zero():
    """Test creating a Cost instance with zero cost."""
    cost = Cost(cost=0.0, model="gpt-4o-mini")
    assert cost.cost == 0.0


def test_cost_creation_negative_fails():
    """Test that negative cost raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Cost(cost=-1.0, model="gpt-4o-mini")

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "greater_than_equal"
    assert "cost" in errors[0]["loc"]


def test_cost_pydantic_features():
    """Test Pydantic features work correctly."""
    cost = Cost(cost=2.5, model="gpt-3.5")

    # Test model_dump
    data = cost.model_dump()
    assert data["cost"] == 2.5
    assert data["model"] == "gpt-3.5"
    assert "timestamp" in data

    # Test model_validate
    cost2 = Cost.model_validate(data)
    assert cost2.cost == cost.cost
    assert cost2.model == cost.model


def test_response_latency_creation_valid():
    """Test creating a valid ResponseLatency instance."""
    latency = ResponseLatency(model="gpt-4o-mini", latency=1.5, response_id="test-123")
    assert latency.latency == 1.5
    assert latency.response_id == "test-123"
    assert latency.model == "gpt-4o-mini"


def test_response_latency_creation_zero():
    """Test creating a ResponseLatency instance with zero latency."""
    latency = ResponseLatency(model="gpt-4o-mini", latency=0.0, response_id="test-123")
    assert latency.latency == 0.0


def test_response_latency_creation_negative_fails():
    """Test that negative latency raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        ResponseLatency(model="gpt-4o-mini", latency=-0.5, response_id="test-123")

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "greater_than_equal"
    assert "latency" in errors[0]["loc"]


def test_response_latency_pydantic_features():
    """Test Pydantic features work correctly."""
    latency = ResponseLatency(model="gpt-4o-mini", latency=2.3, response_id="test-789")

    # Test model_dump
    data = latency.model_dump()
    expected = {"model": "gpt-4o-mini", "latency": 2.3, "response_id": "test-789"}
    assert data == expected

    # Test model_validate
    latency2 = ResponseLatency.model_validate(data)
    assert latency2.latency == latency.latency
    assert latency2.response_id == latency.response_id


def test_token_usage_creation_valid():
    """Test creating a valid TokenUsage instance."""
    usage = TokenUsage(
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=10,
        cache_write_tokens=5,
        context_window=4096,
        per_turn_token=155,
        response_id="test-123",
    )
    assert usage.model == "gpt-4o-mini"
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.cache_read_tokens == 10
    assert usage.cache_write_tokens == 5
    assert usage.context_window == 4096
    assert usage.per_turn_token == 155
    assert usage.response_id == "test-123"


def test_token_usage_creation_zeros():
    """Test creating a TokenUsage instance with zero values."""
    usage = TokenUsage(
        model="gpt-4o-mini",
        prompt_tokens=0,
        completion_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=0,
        per_turn_token=0,
        response_id="test-123",
    )
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0


def test_token_usage_negative_prompt_tokens_fails():
    """Test that negative prompt_tokens raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        TokenUsage(
            model="gpt-4o-mini",
            prompt_tokens=-1,
            completion_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=4096,
            per_turn_token=49,
            response_id="test-123",
        )

    errors = exc_info.value.errors()
    assert any(
        error["type"] == "greater_than_equal" and "prompt_tokens" in error["loc"]
        for error in errors
    )


def test_token_usage_negative_completion_tokens_fails():
    """Test that negative completion_tokens raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        TokenUsage(
            model="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=-1,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=4096,
            per_turn_token=99,
            response_id="test-123",
        )

    errors = exc_info.value.errors()
    assert any(
        error["type"] == "greater_than_equal" and "completion_tokens" in error["loc"]
        for error in errors
    )


def test_token_usage_negative_cache_tokens_fails():
    """Test that negative cache tokens raise ValidationError."""
    with pytest.raises(ValidationError):
        TokenUsage(
            model="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=-1,
            cache_write_tokens=0,
            context_window=4096,
            per_turn_token=149,
            response_id="test-123",
        )

    with pytest.raises(ValidationError):
        TokenUsage(
            model="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=-1,
            context_window=4096,
            per_turn_token=149,
            response_id="test-123",
        )


def test_token_usage_addition():
    """Test that TokenUsage instances can be added together."""
    usage1 = TokenUsage(
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=10,
        cache_write_tokens=5,
        context_window=4096,
        per_turn_token=155,
        response_id="test-1",
    )

    usage2 = TokenUsage(
        model="gpt-4o-mini",
        prompt_tokens=200,
        completion_tokens=75,
        cache_read_tokens=20,
        cache_write_tokens=10,
        context_window=4096,
        per_turn_token=285,
        response_id="test-2",
    )

    combined = usage1 + usage2

    assert combined.model == "gpt-4o-mini"
    assert combined.prompt_tokens == 300
    assert combined.completion_tokens == 125
    assert combined.cache_read_tokens == 30
    assert combined.cache_write_tokens == 15
    assert combined.context_window == 4096
    assert combined.per_turn_token == 285  # Uses other.per_turn_token
    assert combined.response_id == "test-1"  # Should keep first response_id


def test_token_usage_pydantic_features():
    """Test Pydantic features work correctly."""
    usage = TokenUsage(
        model="gpt-3.5",
        prompt_tokens=75,
        completion_tokens=25,
        cache_read_tokens=5,
        cache_write_tokens=2,
        context_window=2048,
        per_turn_token=102,
        response_id="test-456",
    )

    # Test model_dump
    data = usage.model_dump()
    expected = {
        "model": "gpt-3.5",
        "prompt_tokens": 75,
        "completion_tokens": 25,
        "cache_read_tokens": 5,
        "cache_write_tokens": 2,
        "reasoning_tokens": 0,
        "context_window": 2048,
        "per_turn_token": 102,
        "response_id": "test-456",
    }
    assert data == expected

    # Test model_validate
    usage2 = TokenUsage.model_validate(data)
    assert usage2.model == usage.model
    assert usage2.prompt_tokens == usage.prompt_tokens
    assert usage2.completion_tokens == usage.completion_tokens


def test_metrics_creation_empty():
    """Test creating an empty Metrics instance."""
    metrics = Metrics()
    assert metrics.model_name == "default"
    assert metrics.accumulated_cost == 0.0
    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 0
    assert metrics.costs == []
    assert metrics.response_latencies == []


def test_metrics_creation_with_model_name():
    """Test creating a Metrics instance with model name."""
    metrics = Metrics(model_name="gpt-4o-mini")
    assert metrics.model_name == "gpt-4o-mini"
    assert metrics.accumulated_cost == 0.0
    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 0


def test_metrics_add_cost():
    """Test adding cost to metrics."""
    metrics = Metrics()
    metrics.add_cost(5.0)

    assert metrics.accumulated_cost == 5.0
    assert len(metrics.costs) == 1
    assert metrics.costs[0].cost == 5.0
    assert metrics.costs[0].model == "default"


def test_metrics_add_cost_with_model_name():
    """Test adding cost with custom model name."""
    metrics = Metrics(model_name="gpt-4o-mini")
    metrics.add_cost(3.5)

    assert metrics.accumulated_cost == 3.5
    assert len(metrics.costs) == 1
    assert metrics.costs[0].cost == 3.5
    assert metrics.costs[0].model == "gpt-4o-mini"


def test_metrics_add_multiple_costs():
    """Test adding multiple costs."""
    metrics = Metrics()
    metrics.add_cost(2.0)
    metrics.add_cost(3.0)
    metrics.add_cost(1.5)

    assert metrics.accumulated_cost == 6.5
    assert len(metrics.costs) == 3


def test_metrics_add_response_latency():
    """Test adding response latency to metrics."""
    metrics = Metrics()
    metrics.add_response_latency(1.5, "test-123")

    assert len(metrics.response_latencies) == 1
    assert metrics.response_latencies[0].latency == 1.5
    assert metrics.response_latencies[0].response_id == "test-123"


def test_metrics_add_multiple_response_latencies():
    """Test adding multiple response latencies."""
    metrics = Metrics()
    metrics.add_response_latency(1.0, "test-1")
    metrics.add_response_latency(2.5, "test-2")
    metrics.add_response_latency(0.8, "test-3")

    assert len(metrics.response_latencies) == 3
    assert metrics.response_latencies[1].latency == 2.5


def test_metrics_add_token_usage_first_time():
    """Test adding token usage for the first time."""
    metrics = Metrics()
    metrics.add_token_usage(100, 50, 10, 5, 4096, "test-123")

    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 100
    assert metrics.accumulated_token_usage.completion_tokens == 50
    assert metrics.accumulated_token_usage.cache_read_tokens == 10
    assert metrics.accumulated_token_usage.cache_write_tokens == 5
    assert metrics.accumulated_token_usage.context_window == 4096
    assert metrics.accumulated_token_usage.per_turn_token == 150
    assert metrics.accumulated_token_usage.response_id == ""


def test_metrics_add_token_usage_accumulate():
    """Test adding token usage multiple times accumulates correctly."""
    metrics = Metrics()
    metrics.add_token_usage(100, 50, 10, 5, 4096, "test-1")
    metrics.add_token_usage(200, 75, 20, 10, 4096, "test-2")

    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 300
    assert metrics.accumulated_token_usage.completion_tokens == 125
    assert metrics.accumulated_token_usage.cache_read_tokens == 30
    assert metrics.accumulated_token_usage.cache_write_tokens == 15
    assert metrics.accumulated_token_usage.per_turn_token == 275


def test_metrics_merge_empty_metrics():
    """Test merging with empty metrics."""
    metrics1 = Metrics()
    metrics1.add_cost(5.0)

    metrics2 = Metrics()

    metrics1.merge(metrics2)
    assert metrics1.accumulated_cost == 5.0


def test_metrics_merge_with_costs():
    """Test merging metrics with costs."""
    metrics1 = Metrics()
    metrics1.add_cost(5.0)

    metrics2 = Metrics()
    metrics2.add_cost(3.0)

    metrics1.merge(metrics2)
    assert metrics1.accumulated_cost == 8.0
    assert len(metrics1.costs) == 2


def test_metrics_merge_with_token_usage():
    """Test merging metrics with token usage."""
    metrics1 = Metrics()
    metrics1.add_token_usage(100, 50, 10, 5, 4096, "test-1")

    metrics2 = Metrics()
    metrics2.add_token_usage(200, 75, 20, 10, 4096, "test-2")

    metrics1.merge(metrics2)
    assert metrics1.accumulated_token_usage is not None
    assert metrics1.accumulated_token_usage.prompt_tokens == 300
    assert metrics1.accumulated_token_usage.completion_tokens == 125


def test_metrics_merge_with_response_latencies():
    """Test merging metrics with response latencies."""
    metrics1 = Metrics()
    metrics1.add_response_latency(1.0, "test-1")

    metrics2 = Metrics()
    metrics2.add_response_latency(2.0, "test-2")

    metrics1.merge(metrics2)
    assert len(metrics1.response_latencies) == 2
    assert metrics1.response_latencies[0].latency == 1.0
    assert metrics1.response_latencies[1].latency == 2.0


def test_metrics_get_method():
    """Test the get method returns correct data."""
    metrics = Metrics(model_name="gpt-4o-mini")
    metrics.add_cost(5.0)
    metrics.add_token_usage(100, 50, 10, 5, 4096, "test-123")
    metrics.add_response_latency(1.5, "test-123")

    data = metrics.get()

    assert data["accumulated_cost"] == 5.0
    assert data["accumulated_token_usage"]["prompt_tokens"] == 100
    assert len(data["costs"]) == 1
    assert len(data["response_latencies"]) == 1


def test_metrics_diff_method():
    """Test the diff method calculates differences correctly."""
    metrics1 = Metrics()
    metrics1.add_cost(10.0)
    metrics1.add_token_usage(500, 250, 50, 25, 4096, "test-1")

    metrics2 = Metrics()
    metrics2.add_cost(3.0)
    metrics2.add_token_usage(200, 100, 20, 10, 4096, "test-2")

    diff = metrics1.diff(metrics2)

    assert diff.accumulated_cost == 7.0  # 10.0 - 3.0
    assert diff.accumulated_token_usage is not None
    assert diff.accumulated_token_usage.prompt_tokens == 300  # 500 - 200
    assert diff.accumulated_token_usage.completion_tokens == 150  # 250 - 100


def test_metrics_diff_with_none_token_usage():
    """Test diff method when one metrics has None token usage."""
    metrics1 = Metrics()
    metrics1.add_cost(10.0)
    metrics1.add_token_usage(500, 250, 50, 25, 4096, "test-1")

    metrics2 = Metrics()
    metrics2.add_cost(3.0)
    # No token usage added to metrics2

    diff = metrics1.diff(metrics2)

    assert diff.accumulated_cost == 7.0
    assert diff.accumulated_token_usage is not None
    assert diff.accumulated_token_usage.prompt_tokens == 500
    assert diff.accumulated_token_usage.completion_tokens == 250


def test_metrics_deep_copy():
    """Test the deep_copy method creates independent copy."""
    metrics = Metrics(model_name="gpt-4o-mini")
    metrics.add_cost(5.0)
    metrics.add_token_usage(100, 50, 10, 5, 4096, "test-123")

    copied = metrics.deep_copy()

    # Verify copy has same data
    assert copied.model_name == metrics.model_name
    assert copied.accumulated_cost == metrics.accumulated_cost
    assert copied.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage is not None
    assert (
        copied.accumulated_token_usage.prompt_tokens
        == metrics.accumulated_token_usage.prompt_tokens
    )

    # Verify they are independent
    copied.add_cost(2.0)
    assert copied.accumulated_cost == 7.0
    assert metrics.accumulated_cost == 5.0


def test_metrics_pydantic_features():
    """Test Pydantic features work correctly."""
    metrics = Metrics(model_name="gpt-4o-mini")
    metrics.add_cost(5.0)
    metrics.add_token_usage(100, 50, 10, 5, 4096, "test-123")

    # Test model_dump
    data = metrics.model_dump()
    assert data["accumulated_cost"] == 5.0
    assert data["accumulated_token_usage"]["prompt_tokens"] == 100

    # Test model_validate
    metrics2 = Metrics.model_validate(data)
    assert metrics2.model_name == metrics.model_name
    assert metrics2.accumulated_cost == metrics.accumulated_cost
    assert metrics2.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage is not None
    assert (
        metrics2.accumulated_token_usage.prompt_tokens
        == metrics.accumulated_token_usage.prompt_tokens
    )


def test_metrics_validation_errors():
    """Test that validation errors are properly raised."""
    # Test that we can't create metrics with invalid nested data
    with pytest.raises(ValidationError):
        Metrics.model_validate(
            {
                "accumulated_cost": -1.0,  # Should be caught by validation
                "accumulated_token_usage": None,
                "costs": [],
                "response_latencies": [],
                "token_usages": [],
            }
        )


def test_metrics_model_validator():
    """Test the model validator for accumulated_cost consistency."""
    # This should work - cost matches sum of costs
    data = {
        "accumulated_cost": 8.0,
        "accumulated_token_usage": None,
        "costs": [
            {"cost": 5.0, "model": "gpt-4o-mini", "response_id": "test-1"},
            {"cost": 3.0, "model": "gpt-4o-mini", "response_id": "test-2"},
        ],
        "response_latencies": [],
        "token_usages": [],
    }
    metrics = Metrics.model_validate(data)
    assert metrics.accumulated_cost == 8.0


def test_metrics_empty_state_operations():
    """Test operations on empty metrics work correctly."""
    metrics = Metrics()

    # Test get on empty metrics
    data = metrics.get()
    assert data["accumulated_cost"] == 0.0
    assert data["accumulated_token_usage"] is not None

    # Test diff with empty metrics
    other = Metrics()
    diff = metrics.diff(other)
    assert diff.accumulated_cost == 0.0
    assert diff.accumulated_token_usage is not None

    # Test merge with empty metrics
    metrics.merge(other)
    assert metrics.accumulated_cost == 0.0
    assert metrics.accumulated_token_usage is not None


def test_metrics_as_pydantic_field():
    """Test that Metrics can be used as a field in another Pydantic class."""
    from pydantic import BaseModel

    class TestModel(BaseModel):
        name: str
        metrics: Metrics

    # Create a metrics instance
    metrics = Metrics(model_name="gpt-4o-mini")
    metrics.add_cost(5.0)

    # Use it in another model
    test_model = TestModel(name="test", metrics=metrics)
    assert test_model.name == "test"
    assert test_model.metrics.model_name == "gpt-4o-mini"
    assert test_model.metrics.accumulated_cost == 5.0

    # Test serialization/deserialization
    data = test_model.model_dump()
    test_model2 = TestModel.model_validate(data)
    assert test_model2.metrics.accumulated_cost == 5.0


def test_metrics_cost_negative_validation():
    """Test Cost validation with negative values (line 17)."""
    # Test negative cost validation - Pydantic validation happens first
    with pytest.raises(
        ValidationError, match="Input should be greater than or equal to 0"
    ):
        Cost(model="test-model", cost=-1.0)


def test_metrics_accumulated_cost_negative_validation():
    """Test Metrics accumulated cost validation with negative values (line 105)."""
    # Create a metrics instance with negative accumulated cost
    with pytest.raises(
        ValidationError, match="Input should be greater than or equal to 0"
    ):
        Metrics(accumulated_cost=-1.0)


def test_metrics_add_token_usage_none_accumulated():
    """Test adding token usage when accumulated_token_usage is None (line 172)."""
    # Create metrics - it auto-initializes accumulated_token_usage
    metrics = Metrics()
    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 0

    # Add token usage - should update accumulated_token_usage (line 172)
    metrics.add_token_usage(
        prompt_tokens=10,
        completion_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test-response",
    )

    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 10
    assert metrics.accumulated_token_usage.completion_tokens == 5


def test_metrics_merge_max_budget_from_other():
    """Test merging when max_budget_per_task is None in self but set in other."""
    # Create metrics with no max_budget_per_task
    metrics1 = Metrics()
    assert metrics1.max_budget_per_task is None

    # Create metrics with max_budget_per_task
    metrics2 = Metrics(max_budget_per_task=100.0)

    # Merge - should copy max_budget_per_task from other (line 182)
    metrics1.merge(metrics2)
    assert metrics1.max_budget_per_task == 100.0


def test_metrics_merge_accumulated_token_usage_none_self():
    """Test merging when self.accumulated_token_usage is None (line 190)."""
    # Create metrics and manually set accumulated_token_usage to None
    metrics1 = Metrics()
    metrics1.accumulated_token_usage = None

    # Create metrics with accumulated token usage
    metrics2 = Metrics()
    metrics2.add_token_usage(
        prompt_tokens=10,
        completion_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test",
    )

    # Merge - should copy accumulated_token_usage from other (line 190)
    metrics1.merge(metrics2)
    assert metrics1.accumulated_token_usage is not None
    assert metrics1.accumulated_token_usage.prompt_tokens == 10
    assert metrics1.accumulated_token_usage.completion_tokens == 5


def test_metrics_diff_current_usage_not_none():
    """Test diff method when current_usage is not None (lines 274-275)."""
    # Create metrics with accumulated token usage
    metrics1 = Metrics()
    metrics1.add_token_usage(
        prompt_tokens=20,
        completion_tokens=10,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test1",
    )

    # Create another metrics with different usage
    metrics2 = Metrics()
    metrics2.add_token_usage(
        prompt_tokens=10,
        completion_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test2",
    )

    # Calculate diff - should handle current_usage not None (lines 274-275)
    diff = metrics1.diff(metrics2)
    assert diff.accumulated_token_usage is not None
    assert diff.accumulated_token_usage.prompt_tokens == 10
    assert diff.accumulated_token_usage.completion_tokens == 5


def test_metrics_diff_both_usage_none():
    """Test diff method when both accumulated_token_usage are None (lines 276-277)."""
    # Create metrics and manually set accumulated_token_usage to None
    metrics1 = Metrics()
    metrics1.accumulated_token_usage = None
    metrics2 = Metrics()
    metrics2.accumulated_token_usage = None

    # Calculate diff - should handle both None (lines 276-277)
    diff = metrics1.diff(metrics2)
    assert diff.accumulated_token_usage is None


def test_cost_positive_validation():
    """Test Cost model with positive cost (line 17 - positive case)."""
    # Should not raise error for positive cost
    cost = Cost(model="test-model", cost=10.5)
    assert cost.cost == 10.5
    assert cost.model == "test-model"


def test_metrics_accumulated_cost_positive_validation():
    """Test Metrics model with positive accumulated_cost (line 105 - positive case)."""
    # Should not raise error for positive accumulated_cost
    metrics = Metrics(accumulated_cost=15.0)
    assert metrics.accumulated_cost == 15.0


def test_metrics_add_token_usage_with_existing_accumulated():
    """Test add_token_usage when accumulated_token_usage already exists."""
    # Create metrics and add initial usage
    metrics = Metrics()
    metrics.add_token_usage(
        prompt_tokens=10,
        completion_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test1",
    )

    # Add more usage - should trigger line 174 (else branch)
    metrics.add_token_usage(
        prompt_tokens=20,
        completion_tokens=10,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test2",
    )

    # Should have accumulated the usage
    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 30
    assert metrics.accumulated_token_usage.completion_tokens == 15


def test_metrics_add_token_usage_none_accumulated_initial():
    """Test add_token_usage when accumulated_token_usage is None initially."""
    # Create metrics and manually set accumulated_token_usage to None
    metrics = Metrics()
    metrics.accumulated_token_usage = None

    # Add usage - should trigger line 172 (if branch)
    metrics.add_token_usage(
        prompt_tokens=10,
        completion_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=100,
        response_id="test",
    )

    # Should have set the usage
    assert metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 10
    assert metrics.accumulated_token_usage.completion_tokens == 5


def test_cost_validator_positive_path():
    """Test Cost validator positive path."""
    # Create Cost using Pydantic validation to trigger validator
    cost = Cost(model="test-model", cost=5.0)
    assert cost.cost == 5.0
    assert cost.model == "test-model"


def test_metrics_accumulated_cost_validator_positive_path():
    """Test Metrics accumulated_cost validator positive path."""
    # Create Metrics using Pydantic validation to trigger validator
    metrics = Metrics(accumulated_cost=10.0)
    assert metrics.accumulated_cost == 10.0


def test_metrics_diff_current_only_not_none():
    """Test diff method when current has usage but baseline doesn't (line 275)."""
    # Create metrics with usage
    metrics1 = Metrics()
    metrics1.add_token_usage(
        prompt_tokens=15,
        completion_tokens=8,
        cache_read_tokens=2,
        cache_write_tokens=1,
        context_window=200,
        response_id="test",
    )

    # Create baseline metrics with None usage
    metrics2 = Metrics()
    metrics2.accumulated_token_usage = None

    # Calculate diff - should copy current_usage (line 275)
    diff = metrics1.diff(metrics2)
    assert diff.accumulated_token_usage is not None
    assert diff.accumulated_token_usage.prompt_tokens == 15
    assert diff.accumulated_token_usage.completion_tokens == 8
    assert diff.accumulated_token_usage.cache_read_tokens == 2
    assert diff.accumulated_token_usage.cache_write_tokens == 1
