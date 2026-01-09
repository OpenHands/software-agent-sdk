import json
import os
import tempfile
import time
import warnings
from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import ModelResponse, Usage
from pydantic import BaseModel, Field, ValidationError

from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry, _safe_json


@pytest.fixture
def mock_metrics():
    """Create a mock Metrics instance."""
    return Metrics()


@pytest.fixture
def basic_telemetry(mock_metrics):
    """Create a basic Telemetry instance for testing."""
    return Telemetry(model_name="gpt-4o", log_enabled=False, metrics=mock_metrics)


@pytest.fixture
def mock_response():
    """Create a mock ModelResponse for testing."""
    return ModelResponse(
        id="test-response-id",
        choices=[],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )


class TestTelemetryInitialization:
    """Test Telemetry class initialization and configuration."""

    def test_telemetry_default_initialization(self, mock_metrics):
        """Test Telemetry initialization with default values."""
        telemetry = Telemetry(metrics=mock_metrics)

        assert telemetry.model_name == "unknown"
        assert telemetry.log_enabled is False
        assert telemetry.log_dir is None
        assert telemetry.input_cost_per_token is None
        assert telemetry.output_cost_per_token is None
        assert telemetry.metrics == mock_metrics

    def test_telemetry_custom_initialization(self, mock_metrics):
        """Test Telemetry initialization with custom values."""
        telemetry = Telemetry(
            model_name="custom-model",
            log_enabled=True,
            log_dir="/tmp/logs",
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
            metrics=mock_metrics,
        )

        assert telemetry.model_name == "custom-model"
        assert telemetry.log_enabled is True
        assert telemetry.log_dir == "/tmp/logs"
        assert telemetry.input_cost_per_token == 0.001
        assert telemetry.output_cost_per_token == 0.002
        assert telemetry.metrics == mock_metrics

    def test_telemetry_validation_error(self):
        """Test that Telemetry raises ValidationError when metrics is missing."""
        with pytest.raises(ValidationError):
            Telemetry()  # type: ignore

    def test_telemetry_private_attributes(self, basic_telemetry):
        """Test that private attributes are initialized correctly."""
        # Private attributes should be accessible but not serialized
        assert hasattr(basic_telemetry, "_req_start")
        assert hasattr(basic_telemetry, "_req_ctx")
        assert hasattr(basic_telemetry, "_last_latency")

        # Check default values
        assert basic_telemetry._req_start == 0.0
        assert basic_telemetry._req_ctx == {}
        assert basic_telemetry._last_latency == 0.0


class TestTelemetryLifecycle:
    """Test Telemetry lifecycle methods."""

    def test_on_request_basic(self, basic_telemetry):
        """Test on_request method with basic functionality."""
        start_time = time.time()
        basic_telemetry.on_request(None)

        # Should set request start time
        assert basic_telemetry._req_start >= start_time
        assert basic_telemetry._req_ctx == {}

    def test_on_request_with_context(self, basic_telemetry):
        """Test on_request method with telemetry context."""
        telemetry_ctx = {"context_window": 4096, "user_id": "test-user"}
        basic_telemetry.on_request(telemetry_ctx)

        assert basic_telemetry._req_ctx == telemetry_ctx

    def test_on_error_stub(self, basic_telemetry):
        """Test on_error method (currently a stub)."""
        # Should not raise any exceptions
        basic_telemetry.on_error(Exception("test error"))

    @patch("time.time")
    def test_on_response_latency_tracking(
        self, mock_time, basic_telemetry, mock_response
    ):
        """Test that on_response correctly tracks latency."""
        # Set up time sequence
        mock_time.side_effect = [1000.0, 1002.5]  # 2.5 second latency

        basic_telemetry.on_request(None)
        metrics = basic_telemetry.on_response(mock_response)

        assert basic_telemetry._last_latency == 2.5
        assert isinstance(metrics.accumulated_cost, float)

    def test_on_response_with_usage(self, basic_telemetry):
        """Test on_response with usage information."""
        basic_telemetry.on_request({"context_window": 4096})

        # Create a ModelResponse with usage data
        response = ModelResponse(
            id="test-response-id",
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

        basic_telemetry.on_response(response)

        # Should record token usage
        assert len(basic_telemetry.metrics.token_usages) == 1
        token_usage = basic_telemetry.metrics.token_usages[0]
        assert token_usage.prompt_tokens == 100
        assert token_usage.completion_tokens == 50


class TestTelemetryTokenUsage:
    """Test token usage recording functionality."""

    def test_record_usage_basic(self, basic_telemetry):
        """Test basic token usage recording."""
        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

        basic_telemetry._record_usage(usage, "test-id", 4096)

        assert len(basic_telemetry.metrics.token_usages) == 1
        token_usage = basic_telemetry.metrics.token_usages[0]
        assert token_usage.prompt_tokens == 100
        assert token_usage.completion_tokens == 50
        assert token_usage.cache_read_tokens == 0
        assert token_usage.cache_write_tokens == 0
        assert token_usage.context_window == 4096
        assert token_usage.response_id == "test-id"

    def test_record_usage_with_cache_read(self, basic_telemetry):
        """Test token usage recording with cache read tokens."""
        # Create a mock usage with prompt_tokens_details
        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

        # Mock the prompt_tokens_details attribute
        mock_details = MagicMock()
        mock_details.cached_tokens = 25
        usage.prompt_tokens_details = mock_details

        basic_telemetry._record_usage(usage, "test-id", 4096)

        token_usage = basic_telemetry.metrics.token_usages[0]
        assert token_usage.cache_read_tokens == 25

    def test_record_usage_with_cache_write(self, basic_telemetry):
        """Test token usage recording with cache write tokens."""
        from litellm import Usage

        usage = Usage.model_construct(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            model_extra={"cache_creation_input_tokens": 30},
        )
        # Set the attribute that telemetry code expects
        usage._cache_creation_input_tokens = 30

        basic_telemetry._record_usage(usage, "test-id", 4096)

        token_usage = basic_telemetry.metrics.token_usages[0]
        assert token_usage.cache_write_tokens == 30

    def test_record_usage_missing_tokens(self, basic_telemetry):
        """Test token usage recording with missing token counts."""
        usage = Usage()  # Empty usage

        basic_telemetry._record_usage(usage, "test-id", 4096)

        token_usage = basic_telemetry.metrics.token_usages[0]
        assert token_usage.prompt_tokens == 0
        assert token_usage.completion_tokens == 0

    def test_record_usage_with_none_context_window(self, basic_telemetry):
        """Test token usage recording with None context_window.

        This tests issue #905 where unmapped models have
        max_input_tokens=None. The fix ensures that None values
        are handled by converting them to 0 before reaching telemetry.
        """
        usage = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        # Simulate the case where context_window is None (unmapped model)
        # This should raise a validation error at the telemetry level
        # The fix is applied at the LLM level before calling _record_usage
        with pytest.raises(ValidationError, match="Input should be a valid integer"):
            basic_telemetry._record_usage(usage, "test-id", None)  # type: ignore[arg-type]


class TestTelemetryCostCalculation:
    """Test cost calculation functionality."""

    def test_compute_cost_with_custom_rates(self, mock_metrics):
        """Test cost computation with custom input/output rates."""
        telemetry = Telemetry(
            model_name="gpt-4o",
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
            metrics=mock_metrics,
        )

        mock_response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="gpt-4o",
            object="chat.completion",
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

        with patch(
            "openhands.sdk.llm.utils.telemetry.litellm_completion_cost"
        ) as mock_cost:
            mock_cost.return_value = 0.25
            telemetry._compute_cost(mock_response)

            # Should call litellm with custom cost per token
            mock_cost.assert_called_once()
            call_kwargs = mock_cost.call_args[1]
            assert "custom_cost_per_token" in call_kwargs
            # CostPerToken is a TypedDict, so check it has the expected keys
            cost_per_token = call_kwargs["custom_cost_per_token"]
            assert "input_cost_per_token" in cost_per_token
            assert "output_cost_per_token" in cost_per_token

    def test_compute_cost_from_headers(self, basic_telemetry):
        """Test cost extraction from response headers."""
        mock_response = MagicMock()
        mock_response._hidden_params = {
            "additional_headers": {"llm_provider-x-litellm-response-cost": "0.15"}
        }

        cost = basic_telemetry._compute_cost(mock_response)
        assert cost == 0.15

    def test_compute_cost_litellm_fallback(self, basic_telemetry):
        """Test fallback to litellm cost calculator."""
        mock_response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="gpt-4o",
            object="chat.completion",
        )

        with patch(
            "openhands.sdk.llm.utils.telemetry.litellm_completion_cost"
        ) as mock_cost:
            mock_cost.return_value = 0.30
            cost = basic_telemetry._compute_cost(mock_response)

            assert cost == 0.30
            mock_cost.assert_called_once()

    def test_compute_cost_failure_handling(self, basic_telemetry):
        """Test cost calculation failure handling."""
        mock_response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="gpt-4o",
            object="chat.completion",
        )

        with patch(
            "openhands.sdk.llm.utils.telemetry.litellm_completion_cost"
        ) as mock_cost:
            mock_cost.side_effect = Exception("Cost calculation failed")

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                cost = basic_telemetry._compute_cost(mock_response)

                assert cost is None
                assert len(w) == 1
                assert "Cost calculation failed" in str(w[0].message)

    def test_compute_cost_model_name_processing(self, mock_metrics):
        """Test that model name is processed correctly for litellm."""
        telemetry = Telemetry(model_name="provider/gpt-4o-mini", metrics=mock_metrics)

        mock_response = ModelResponse(
            id="test-id",
            choices=[],
            created=1234567890,
            model="gpt-4o-mini",
            object="chat.completion",
        )

        with patch(
            "openhands.sdk.llm.utils.telemetry.litellm_completion_cost"
        ) as mock_cost:
            mock_cost.return_value = 0.10
            telemetry._compute_cost(mock_response)

            # Should strip provider prefix
            call_kwargs = mock_cost.call_args[1]
            assert call_kwargs["model"] == "gpt-4o-mini"


class TestTelemetryLogging:
    """Test telemetry logging functionality."""

    def test_log_completion_disabled(self, basic_telemetry, mock_response):
        """Test that logging is skipped when disabled."""
        basic_telemetry.on_request({"test": "context"})

        # Should not create any files when log_enabled is False
        with tempfile.TemporaryDirectory() as temp_dir:
            basic_telemetry.log_dir = temp_dir
            # Use on_response instead of _log_completion directly to test the full flow
            basic_telemetry.on_response(mock_response)

            # No files should be created since logging is disabled
            assert len(os.listdir(temp_dir)) == 0

    def test_log_completion_no_directory(self, mock_metrics, mock_response):
        """Test logging when no log directory is set."""
        telemetry = Telemetry(
            model_name="gpt-4o", log_enabled=True, log_dir=None, metrics=mock_metrics
        )

        # Should return early without error
        telemetry.log_llm_call(mock_response, 0.25)

    def test_log_completion_success(self, mock_metrics, mock_response):
        """Test successful completion logging."""
        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Telemetry(
                model_name="gpt-4o",
                log_enabled=True,
                log_dir=temp_dir,
                metrics=mock_metrics,
            )

            # Set up context and latency
            telemetry.on_request({"user_id": "test-user", "context_window": 4096})
            telemetry._last_latency = 1.5

            telemetry.log_llm_call(mock_response, 0.25)

            # Should create a log file
            files = os.listdir(temp_dir)
            assert len(files) == 1

            # Check file content
            with open(os.path.join(temp_dir, files[0])) as f:
                data = json.loads(f.read())

            assert data["user_id"] == "test-user"
            assert data["context_window"] == 4096
            assert data["cost"] == 0.25
            assert data["latency_sec"] == 1.5
            assert "response" in data
            assert "timestamp" in data

    def test_log_completion_with_raw_response(self, mock_metrics, mock_response):
        """Test logging with raw response included."""
        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Telemetry(
                model_name="gpt-4o",
                log_enabled=True,
                log_dir=temp_dir,
                metrics=mock_metrics,
            )

            raw_response = ModelResponse(
                id="raw-id",
                choices=[],
                created=1234567890,
                model="gpt-4o",
                object="chat.completion",
            )

            telemetry.on_request({})
            telemetry.log_llm_call(mock_response, 0.25, raw_resp=raw_response)

            files = os.listdir(temp_dir)
            with open(os.path.join(temp_dir, files[0])) as f:
                data = json.loads(f.read())

            assert "raw_response" in data

    def test_log_completion_with_pydantic_objects_in_context(
        self, mock_metrics, mock_response
    ):
        """
        Ensure logging works when log_ctx contains Pydantic models with
        excluded fields. This simulates the remote-run case where tools
        (Pydantic models with excluded runtime-only fields like executors)
        are included in the log context. Using Pydantic's model_dump should
        avoid circular references.
        """

        class SelfReferencingModel(BaseModel):
            name: str
            # Simulate an executor-like field that should not be serialized
            executor: object | None = Field(default=None, exclude=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Telemetry(
                model_name="gpt-4o",
                log_enabled=True,
                log_dir=temp_dir,
                metrics=mock_metrics,
            )

            # Create a self-referencing instance via an excluded field
            m = SelfReferencingModel(name="tool-like")
            m.executor = m  # would create a cycle if serialized via __dict__

            telemetry.on_request({"tools": [m]})

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                telemetry.log_llm_call(mock_response, 0.25)

            # Should not raise circular reference warnings
            msgs = [str(x.message) for x in w]
            assert not any("Circular reference detected" in s for s in msgs)

            # Log file should be created and readable JSON
            files = os.listdir(temp_dir)
            assert len(files) == 1
            with open(os.path.join(temp_dir, files[0])) as f:
                data = json.loads(f.read())
            assert "response" in data

        """Test that model names with slashes are sanitized in filenames."""
        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Telemetry(
                model_name="provider/gpt-4o",
                log_enabled=True,
                log_dir=temp_dir,
                metrics=mock_metrics,
            )

            telemetry.on_request({})
            telemetry.log_llm_call(mock_response, 0.25)

            files = os.listdir(temp_dir)
            assert len(files) == 1
            # Should replace '/' with '__'
            assert "provider__gpt-4o" in files[0]

    def test_log_completion_error_handling(self, mock_metrics, mock_response):
        """Test logging error handling."""
        # Use a guaranteed-invalid log_dir by pointing at a regular file path
        # rather than a directory. This avoids reliance on environment-specific
        # directories that may unexpectedly exist or be writable in CI.
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            bogus_path = tmp.name
            telemetry = Telemetry(
                model_name="gpt-4o",
                log_enabled=True,
                log_dir=bogus_path,
                metrics=mock_metrics,
            )

            telemetry.on_request({})

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                telemetry.log_llm_call(mock_response, 0.25)

                # Should issue a warning but not crash
                assert len(w) == 1
                assert "Telemetry logging failed" in str(w[0].message)
        finally:
            try:
                tmp.close()
            except Exception:
                pass
            try:
                os.unlink(tmp.name)
            except Exception:
                pass


class TestTelemetryIntegration:
    """Test full telemetry integration scenarios."""

    def test_full_request_response_cycle(self, mock_metrics):
        """Test complete request-response cycle with all features."""
        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Telemetry(
                model_name="gpt-4o",
                log_enabled=True,
                log_dir=temp_dir,
                input_cost_per_token=0.001,
                output_cost_per_token=0.002,
                metrics=mock_metrics,
            )

            # Start request
            telemetry_ctx = {"user_id": "test-user", "context_window": 4096}
            telemetry.on_request(telemetry_ctx)

            # Create response with usage (ModelResponse format)
            response = ModelResponse(
                id="test-response-id",
                usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            )

            with patch(
                "openhands.sdk.llm.utils.telemetry.litellm_completion_cost"
            ) as mock_cost:
                mock_cost.return_value = 0.25
                metrics = telemetry.on_response(response)  # type: ignore

            # Verify all aspects
            assert metrics.accumulated_cost == 0.25
            assert len(telemetry.metrics.token_usages) == 1
            assert len(telemetry.metrics.costs) == 1
            assert len(telemetry.metrics.response_latencies) == 1

            # Verify log file was created
            files = os.listdir(temp_dir)
            assert len(files) == 1

    def test_multiple_requests(self, basic_telemetry):
        """Test handling multiple sequential requests."""
        responses = []

        for i in range(3):
            basic_telemetry.on_request({"request_id": i})

            response = ModelResponse(
                id=f"response-{i}",
                usage=Usage(
                    prompt_tokens=100 + i * 10,
                    completion_tokens=50 + i * 5,
                    total_tokens=150 + i * 15,
                ),
            )

            with patch(
                "openhands.sdk.llm.utils.telemetry.litellm_completion_cost"
            ) as mock_cost:
                mock_cost.return_value = 0.1 + i * 0.05
                cost = basic_telemetry.on_response(response)
                responses.append((response, cost))

        # Should have recorded all requests
        assert len(basic_telemetry.metrics.token_usages) == 3
        assert len(basic_telemetry.metrics.costs) == 3
        assert len(basic_telemetry.metrics.response_latencies) == 3

        # Verify accumulated metrics
        total_cost = sum(cost.cost for cost in basic_telemetry.metrics.costs)
        assert abs(total_cost - 0.45) < 1e-10  # Handle floating point precision


class TestSafeJsonFunction:
    """Test the _safe_json utility function."""

    def test_safe_json_with_dict_object(self):
        """Test _safe_json with object that has __dict__."""

        class TestObj:
            def __init__(self):
                self.attr1: str = "value1"
                self.attr2: int = 42

        obj = TestObj()
        result = _safe_json(obj)

        assert result == {"attr1": "value1", "attr2": 42}

    def test_safe_json_without_dict(self):
        """Test _safe_json with object that doesn't have __dict__."""
        obj = 42
        result = _safe_json(obj)

        assert result == "42"

    def test_safe_json_with_exception(self):
        """Test _safe_json when __dict__ access raises exception."""

        class BadObj:
            def __getattribute__(self, name):  # type: ignore
                if name == "__dict__":
                    raise Exception("Cannot access __dict__")
                return super().__getattribute__(name)

        obj = BadObj()
        result = _safe_json(obj)

        # Should fall back to str()
        assert isinstance(result, str)


class TestTelemetryEdgeCases:
    """Test edge cases and error conditions."""

    def test_log_completions_no_serialization_warnings(self, mock_metrics):
        """Test logging completions without Pydantic serialization warnings.

        This reproduces the issue where logging completions with nested Message
        and Choices objects caused PydanticSerializationUnexpectedValue warnings.
        """
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Telemetry(
                model_name="gpt-4o",
                log_enabled=True,
                log_dir=temp_dir,
                metrics=mock_metrics,
            )

            # Create a realistic ModelResponse with nested Message and Choices
            message = LiteLLMMessage(
                content="Test response content",
                role="assistant",
                tool_calls=None,
                function_call=None,
            )
            choice = Choices(
                finish_reason="stop",
                index=0,
                message=message,
                logprobs=None,
            )
            usage = Usage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
            response = ModelResponse(
                id="test-response-id",
                choices=[choice],
                created=1234567890,
                model="gpt-4o",
                object="chat.completion",
                usage=usage,
            )

            telemetry.on_request({"user_id": "test-user", "context_window": 4096})
            telemetry._last_latency = 1.5

            # This should not produce any Pydantic serialization warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                telemetry.log_llm_call(response, 0.25)

                # Check that no Pydantic serialization warnings were raised
                pydantic_warnings = [
                    warning
                    for warning in w
                    if "PydanticSerializationUnexpectedValue" in str(warning.message)
                    or "Circular reference detected" in str(warning.message)
                ]
                if pydantic_warnings:
                    for pw in pydantic_warnings:
                        print(f"Warning: {pw.message}")
                assert len(pydantic_warnings) == 0, (
                    f"Got unexpected serialization warnings: {pydantic_warnings}"
                )

            # Verify the log file was created successfully
            files = os.listdir(temp_dir)
            assert len(files) == 1

            # Verify the content can be read back
            with open(os.path.join(temp_dir, files[0])) as f:
                data = json.loads(f.read())
                assert "response" in data
                assert data["cost"] == 0.25

    def test_on_response_without_on_request(self, basic_telemetry, mock_response):
        """Test on_response called without prior on_request."""
        # Should not crash, should use current time for latency calculation
        metrics = basic_telemetry.on_response(mock_response)

        assert isinstance(metrics.accumulated_cost, float)
        # Latency might be very small or even negative due to timing precision
        # The important thing is that it doesn't crash
        assert isinstance(basic_telemetry._last_latency, float)

    def test_response_id_extraction_edge_cases(self, basic_telemetry):
        """Test response ID extraction from various response formats."""
        # Test with ModelResponse with ID
        response_with_id = ModelResponse(id="model-response-id", usage=None)
        basic_telemetry.on_request({})
        basic_telemetry.on_response(response_with_id)

        # Test with ModelResponse missing ID
        response_no_id = ModelResponse(usage=None)
        basic_telemetry.on_request({})
        basic_telemetry.on_response(response_no_id)

        # Test with non-ModelResponse object
        with pytest.raises(ValidationError):
            mock_response = MagicMock()
            basic_telemetry.on_request({})
            basic_telemetry.on_response(mock_response)

        # Should have recorded latencies for all cases
        assert len(basic_telemetry.metrics.response_latencies) == 2

    def test_usage_extraction_edge_cases(self, basic_telemetry):
        """Test usage extraction from various response formats."""
        # Test with dict response containing usage
        response = ModelResponse(
            id="test-id",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        )

        basic_telemetry.on_request({"context_window": 4096})
        basic_telemetry.on_response(response)
        assert len(basic_telemetry.metrics.token_usages) == 1

        # Test with dict response without usage
        response_no_usage = ModelResponse(id="no-usage-id", usage=None)
        basic_telemetry.on_request({})
        basic_telemetry.on_response(response_no_usage)

        # Should still have only one token usage record
        assert len(basic_telemetry.metrics.token_usages) == 1

    def test_cost_calculation_with_zero_cost(self, basic_telemetry, mock_response):
        """Test cost calculation when cost is zero or None."""
        with patch.object(basic_telemetry, "_compute_cost", return_value=None):
            metrics = basic_telemetry.on_response(mock_response)

            assert metrics.accumulated_cost == 0.0
            # Should not add to costs list when cost is None
            assert len(basic_telemetry.metrics.costs) == 0

        with patch.object(basic_telemetry, "_compute_cost", return_value=0.0):
            metrics = basic_telemetry.on_response(mock_response)

            assert metrics.accumulated_cost == 0.0
            # Should NOT add zero cost to costs list (0.0 is falsy)
            assert len(basic_telemetry.metrics.costs) == 0


class TestTelemetryCallbacks:
    """Test callback functionality for log streaming and stats updates."""

    def test_set_log_callback(self, basic_telemetry):
        """Test setting log callback."""
        callback_called = []

        def log_callback(filename: str, log_data: str):
            callback_called.append((filename, log_data))

        basic_telemetry.set_log_completions_callback(log_callback)
        assert basic_telemetry._log_completions_callback == log_callback

        # Clear callback
        basic_telemetry.set_log_completions_callback(None)
        assert basic_telemetry._log_completions_callback is None

    def test_set_stats_update_callback(self, basic_telemetry):
        """Test setting stats update callback."""
        callback_called = []

        def stats_callback():
            callback_called.append(True)

        basic_telemetry.set_stats_update_callback(stats_callback)
        assert basic_telemetry._stats_update_callback == stats_callback

        # Clear callback
        basic_telemetry.set_stats_update_callback(None)
        assert basic_telemetry._stats_update_callback is None

    def test_stats_update_callback_triggered_on_response(
        self, basic_telemetry, mock_response
    ):
        """Test that stats update callback is triggered on response."""
        callback_called = []

        def stats_callback():
            callback_called.append(True)

        basic_telemetry.set_stats_update_callback(stats_callback)
        basic_telemetry.on_request(None)
        basic_telemetry.on_response(mock_response)

        # Callback should be triggered once after response
        assert len(callback_called) == 1

    def test_stats_update_callback_exception_handling(
        self, basic_telemetry, mock_response
    ):
        """Test that exceptions in stats callback don't break on_response."""

        def failing_callback():
            raise Exception("Callback failed")

        basic_telemetry.set_stats_update_callback(failing_callback)
        basic_telemetry.on_request(None)

        # Should not raise exception even if callback fails
        metrics = basic_telemetry.on_response(mock_response)
        assert isinstance(metrics, Metrics)
