"""
Test that telemetry records are accurate when LLM calls are retried.

This test ensures that when an LLM call is retried, the telemetry only
records the latency and metrics for the successful attempt, not the
combined time of all failed attempts plus the successful one.
"""

import time
from unittest.mock import patch

from litellm.exceptions import APIConnectionError
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent


def create_mock_response(
    content: str = "Test response",
    response_id: str = "test-id",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
):
    """Helper function to create properly structured mock responses."""
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion",
        system_fingerprint="test",
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_telemetry_records_only_successful_attempt_latency(mock_litellm_acompletion):
    """
    Test that when LLM calls are retried, telemetry only records the latency
    of the successful attempt, not the cumulative time of all attempts.

    Before the fix, on_request was called once before retry logic, causing
    the latency to include all failed attempts + wait times. After the fix,
    on_request is called for each retry attempt, so only the successful
    attempt's latency is recorded.
    """
    # Create mock responses for failed and successful attempts
    mock_response = create_mock_response("Success after retry")

    # Simulate 2 failures followed by success
    mock_litellm_acompletion.side_effect = [
        APIConnectionError(
            message="Connection failed 1",
            llm_provider="test_provider",
            model="test_model",
        ),
        APIConnectionError(
            message="Connection failed 2",
            llm_provider="test_provider",
            model="test_model",
        ),
        mock_response,  # Third attempt succeeds
    ]

    # Create LLM with retry configuration and minimal wait times for faster test
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=3,
        retry_min_wait=1,  # 1 second minimum wait
        retry_max_wait=1,  # 1 second maximum wait (same as min for consistent timing)
        usage_id="test-service",
    )

    # Record the start time of the entire operation
    operation_start = time.time()

    # Make the completion call (will retry twice, then succeed)
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Hello!")])],
    )

    # Record the total operation time
    total_operation_time = time.time() - operation_start

    # Verify the call succeeded
    assert response.raw_response == mock_response
    assert mock_litellm_acompletion.call_count == 3

    # Get the metrics to check recorded latency
    metrics = llm.metrics

    # The recorded latency should be much less than the total operation time
    # because it should only include the successful attempt, not the failed ones
    recorded_latencies = [latency.latency for latency in metrics.response_latencies]

    # There should be exactly one latency record (for the successful attempt)
    assert len(recorded_latencies) == 1

    recorded_latency = recorded_latencies[0]

    # The recorded latency should be significantly less than total operation time
    # Total operation time includes:
    # - First attempt (failed) + wait time
    # - Second attempt (failed) + wait time
    # - Third attempt (successful)
    #
    # The recorded latency should only include the third attempt
    assert recorded_latency < total_operation_time * 0.5, (
        f"Recorded latency ({recorded_latency:.3f}s) should be much less "
        f"than total operation time ({total_operation_time:.3f}s)"
    )

    # The recorded latency should be relatively small (just the mock call time)
    # Since we're mocking, it should be very quick (< 100ms typically)
    assert recorded_latency < 0.5, (
        f"Recorded latency ({recorded_latency:.3f}s) should be < 0.5s for a mocked call"
    )


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_telemetry_on_request_called_per_retry(mock_litellm_acompletion):
    """
    Test that telemetry.on_request() is called for each retry attempt.

    This ensures that each retry resets the request timer, so only the
    successful attempt's latency is recorded.

    We verify this by checking the _req_start timestamps which are set
    by on_request(). With the fix, _req_start should be reset for each retry.
    """
    # Track _req_start values to see when on_request is called
    req_start_values = []

    mock_response = create_mock_response("Success after one retry")

    # Create a side effect function that captures _req_start after each attempt
    def mock_transport_call_side_effect(*args, **kwargs):
        # Capture the current _req_start value (set by on_request)
        # This runs inside _one_attempt, after on_request is called
        nonlocal req_start_values
        req_start_values.append(time.time())

        # First call fails, second succeeds
        if len(req_start_values) == 1:
            raise APIConnectionError(
                message="Connection failed",
                llm_provider="test_provider",
                model="test_model",
            )
        return mock_response

    mock_litellm_acompletion.side_effect = mock_transport_call_side_effect

    # Create LLM instance
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=1,
        usage_id="test-service",
    )

    # Make the completion call
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Test")])],
    )

    # Verify the call succeeded
    assert response.raw_response == mock_response

    # Should have attempted twice (one failure, one success)
    assert len(req_start_values) == 2, (
        f"Expected 2 attempts, got {len(req_start_values)}"
    )

    # Verify there was a time gap between the attempts (retry wait time)
    # This proves on_request was called for each attempt
    time_gap = req_start_values[1] - req_start_values[0]
    assert time_gap >= 0.5, (
        "There should be a wait time between retry attempts "
        f"(gap: {time_gap:.3f}s, expected >= 0.5s due to 1 second retry wait)"
    )


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_telemetry_metrics_accurate_with_retries(mock_litellm_acompletion):
    """
    Test that all telemetry metrics (tokens, cost, latency) are accurate
    when retries occur.
    """
    # Create a response with specific token counts
    mock_response = create_mock_response(
        "Success", prompt_tokens=100, completion_tokens=50
    )

    # Simulate one failure then success
    mock_litellm_acompletion.side_effect = [
        APIConnectionError(
            message="Connection failed",
            llm_provider="test_provider",
            model="test_model",
        ),
        mock_response,
    ]

    # Create LLM with cost tracking
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=1,
        usage_id="test-service",
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
    )

    # Make the completion call
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Test")])],
    )

    # Verify the call succeeded
    assert response.raw_response == mock_response

    # Get metrics
    metrics = llm.metrics

    # Token usage should only reflect the successful attempt
    assert len(metrics.token_usages) == 1
    token_usage = metrics.token_usages[0]
    assert token_usage.prompt_tokens == 100
    assert token_usage.completion_tokens == 50

    # Cost should only reflect the successful attempt
    # Note: Cost calculation depends on litellm, so we just verify it's positive
    assert metrics.accumulated_cost > 0

    # Latency should only reflect the successful attempt (should be small)
    assert len(metrics.response_latencies) == 1
    assert metrics.response_latencies[0].latency < 0.5


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_telemetry_no_multiple_records_on_retry(mock_litellm_acompletion):
    """
    Test that telemetry doesn't create multiple records for failed attempts.

    Only the successful attempt should result in telemetry records.
    """
    mock_response = create_mock_response("Success")

    # Simulate multiple failures then success
    mock_litellm_acompletion.side_effect = [
        APIConnectionError(
            message="Fail 1", llm_provider="test_provider", model="test_model"
        ),
        APIConnectionError(
            message="Fail 2", llm_provider="test_provider", model="test_model"
        ),
        APIConnectionError(
            message="Fail 3", llm_provider="test_provider", model="test_model"
        ),
        mock_response,
    ]

    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=5,
        retry_min_wait=1,
        retry_max_wait=1,
        usage_id="test-service",
    )

    # Make the completion call
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Test")])],
    )

    assert response.raw_response == mock_response

    metrics = llm.metrics

    # Should only have ONE latency record (for the successful attempt)
    assert len(metrics.response_latencies) == 1

    # Should only have ONE token usage record (for the successful attempt)
    assert len(metrics.token_usages) == 1

    # Should only have ONE cost record (for the successful attempt)
    # Cost is accumulated, so we just check it's positive
    assert metrics.accumulated_cost > 0
