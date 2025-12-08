"""Tests for Minimax token usage tracking bug fix."""

from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry


class TestMinimaxTokenUsage:
    """Test token usage extraction for Minimax responses."""

    @pytest.fixture
    def mock_metrics(self):
        """Create a mock Metrics instance."""
        return Metrics(model_name="anthropic/MiniMax-M2")

    @pytest.fixture
    def minimax_telemetry(self, mock_metrics):
        """Create a Telemetry instance for Minimax."""
        return Telemetry(
            model_name="anthropic/MiniMax-M2", log_enabled=False, metrics=mock_metrics
        )

    def test_minimax_response_with_zero_usage_bug_reproduction(self, minimax_telemetry):
        """Test that reproduces the actual Minimax bug where responses have zero
        usage."""
        # This reproduces the exact bug described in the issue:
        # Minimax returns responses but with zero token usage, even though actual
        # tokens were used
        mock_response = ModelResponse(
            id="minimax-response-id",
            choices=[],
            created=1234567890,
            model="anthropic/MiniMax-M2",
            object="chat.completion",
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )

        # Set up a mock token estimation callback
        def mock_token_estimator(messages):
            # Simulate estimating tokens based on message content
            return 100  # Return a reasonable estimate

        minimax_telemetry.set_token_estimation_callback(mock_token_estimator)

        # Simulate request with messages (this is what would normally be passed)
        minimax_telemetry.on_request(
            {
                "messages": [{"role": "user", "content": "Hello, how are you?"}],
                "context_window": 4096,
            }
        )
        minimax_telemetry.on_response(mock_response)

        # After the fix: token usage should be estimated and recorded
        assert len(minimax_telemetry.metrics.token_usages) == 1
        token_usage = minimax_telemetry.metrics.token_usages[0]
        assert token_usage.prompt_tokens == 100  # Estimated
        assert token_usage.completion_tokens == 0  # Can't estimate completion tokens
        assert minimax_telemetry.metrics.accumulated_token_usage.prompt_tokens == 100

    def test_minimax_response_with_actual_usage(self, minimax_telemetry):
        """Test that Minimax responses with actual usage are handled correctly."""
        # Simulate a Minimax response that should have token usage but returns zero
        # This is what we expect to happen after the fix
        mock_response = ModelResponse(
            id="minimax-response-id",
            choices=[],
            created=1234567890,
            model="anthropic/MiniMax-M2",
            object="chat.completion",
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

        minimax_telemetry.on_request({"context_window": 4096})
        minimax_telemetry.on_response(mock_response)

        # This should work correctly
        assert len(minimax_telemetry.metrics.token_usages) == 1
        token_usage = minimax_telemetry.metrics.token_usages[0]
        assert token_usage.prompt_tokens == 100
        assert token_usage.completion_tokens == 50

    def test_minimax_response_with_none_usage(self, minimax_telemetry):
        """Test that Minimax responses with None usage are handled correctly."""
        # Simulate a Minimax response with no usage field at all
        mock_response = ModelResponse(
            id="minimax-response-id",
            choices=[],
            created=1234567890,
            model="anthropic/MiniMax-M2",
            object="chat.completion",
            usage=None,
        )

        minimax_telemetry.on_request({"context_window": 4096})
        minimax_telemetry.on_response(mock_response)

        # Should not record any token usage
        assert len(minimax_telemetry.metrics.token_usages) == 0
        assert minimax_telemetry.metrics.accumulated_token_usage.prompt_tokens == 0
        assert minimax_telemetry.metrics.accumulated_token_usage.completion_tokens == 0

    def test_minimax_response_with_custom_usage_format(self, minimax_telemetry):
        """Test handling of Minimax-specific usage format if it exists."""
        # Create a mock response that might have Minimax-specific usage format
        mock_response = ModelResponse(
            id="minimax-response-id",
            choices=[],
            created=1234567890,
            model="anthropic/MiniMax-M2",
            object="chat.completion",
        )

        # Simulate a custom usage format that Minimax might use
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = None
        mock_usage.completion_tokens = None
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.total_tokens = 150

        # Create a new response with the custom usage
        mock_response = ModelResponse(
            id="minimax-response-id",
            choices=[],
            created=1234567890,
            model="MiniMax-M2",
            object="chat.completion",
            usage=mock_usage,
        )

        minimax_telemetry.on_request({"context_window": 4096})
        minimax_telemetry.on_response(mock_response)

        # Should extract tokens from input_tokens/output_tokens format
        assert len(minimax_telemetry.metrics.token_usages) == 1
        token_usage = minimax_telemetry.metrics.token_usages[0]
        assert token_usage.prompt_tokens == 100
        assert token_usage.completion_tokens == 50

    def test_has_meaningful_usage_with_zero_tokens(self, minimax_telemetry):
        """Test _has_meaningful_usage method with zero token counts."""
        # Test with zero tokens - should return False
        usage = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        assert not minimax_telemetry._has_meaningful_usage(usage)

    def test_has_meaningful_usage_with_none_tokens(self, minimax_telemetry):
        """Test _has_meaningful_usage method with None token counts."""
        # Test with None tokens - should return False
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = None
        mock_usage.completion_tokens = None
        mock_usage.input_tokens = None
        mock_usage.output_tokens = None
        assert not minimax_telemetry._has_meaningful_usage(mock_usage)

    def test_has_meaningful_usage_with_input_output_tokens(self, minimax_telemetry):
        """Test _has_meaningful_usage method with input_tokens/output_tokens format."""
        # Test with input_tokens/output_tokens format
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = None
        mock_usage.completion_tokens = None
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        assert minimax_telemetry._has_meaningful_usage(mock_usage)

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_llm_integration_with_minimax_zero_usage(self, mock_completion):
        """Test that LLM class properly handles Minimax zero usage with token
        estimation."""
        # Create a Minimax LLM instance
        llm = LLM(
            model="anthropic/MiniMax-M2",
            api_key=SecretStr("test-key"),
            base_url="https://api.minimax.io/anthropic",
        )

        # Mock the litellm response with zero usage (simulating Minimax behavior)
        mock_response = ModelResponse(
            id="minimax-response-id",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! I'm doing well, thank you for asking.",
                    },
                    "finish_reason": "stop",
                }
            ],
            created=1234567890,
            model="anthropic/MiniMax-M2",
            object="chat.completion",
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )
        mock_completion.return_value = mock_response

        # Mock token_counter to return a reasonable estimate
        with patch("openhands.sdk.llm.llm.token_counter", return_value=25):
            from openhands.sdk.llm.message import Message, TextContent

            messages = [
                Message(role="user", content=[TextContent(text="Hello, how are you?")])
            ]

            # Make the completion call
            llm.completion(messages)

            # Verify that token usage was estimated and recorded
            assert len(llm.metrics.token_usages) == 1
            token_usage = llm.metrics.token_usages[0]
            assert token_usage is not None
            assert token_usage.prompt_tokens == 25  # Estimated by token_counter
            assert (
                token_usage.completion_tokens == 0
            )  # Can't estimate completion tokens
            assert llm.metrics.accumulated_token_usage is not None
            assert llm.metrics.accumulated_token_usage.prompt_tokens == 25
