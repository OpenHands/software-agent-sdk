"""Tests for LLM timeout configuration."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent


# Default timeout in seconds (5 minutes)
DEFAULT_LLM_TIMEOUT_SECONDS = 300


class TestLLMTimeoutDefaults:
    """Tests for default LLM timeout behavior."""

    def test_default_timeout_is_5_minutes(self):
        """Test that the default LLM timeout is 300 seconds (5 minutes).

        This test ensures that LLM requests have a reasonable default timeout
        to prevent indefinitely hanging requests that could cause runtime
        idle detection to kill active runtimes.

        See: https://github.com/OpenHands/software-agent-sdk/issues/1633
        """
        llm = LLM(model="gpt-4o-mini", usage_id="test-llm")

        assert llm.timeout == DEFAULT_LLM_TIMEOUT_SECONDS, (
            f"Expected default timeout of {DEFAULT_LLM_TIMEOUT_SECONDS}s (5 minutes), "
            f"but got {llm.timeout}. "
            "A reasonable default timeout is needed to prevent LLM calls from "
            "hanging indefinitely and causing runtime idle detection issues."
        )

    def test_timeout_can_be_overridden(self):
        """Test that the timeout can be explicitly set to a custom value."""
        custom_timeout = 600  # 10 minutes
        llm = LLM(model="gpt-4o-mini", usage_id="test-llm", timeout=custom_timeout)

        assert llm.timeout == custom_timeout

    def test_timeout_can_be_set_to_none_for_no_timeout(self):
        """Test that timeout can be explicitly set to None to disable timeout.

        Users who need very long LLM calls (e.g., extended reasoning with high
        thinking budgets) can explicitly disable the timeout by setting it to None.
        """
        llm = LLM(model="gpt-4o-mini", usage_id="test-llm", timeout=None)

        # When explicitly set to None, it should remain None
        assert llm.timeout is None

    def test_timeout_validation_rejects_negative_values(self):
        """Test that negative timeout values are rejected."""
        with pytest.raises(Exception):  # ValidationError from pydantic
            LLM(model="gpt-4o-mini", usage_id="test-llm", timeout=-1)

    def test_timeout_accepts_zero(self):
        """Test that zero timeout is valid (immediate timeout)."""
        llm = LLM(model="gpt-4o-mini", usage_id="test-llm", timeout=0)
        assert llm.timeout == 0


class TestLLMTimeoutPassthrough:
    """Tests that timeout is correctly passed to litellm."""

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_default_timeout_passed_to_litellm(self, mock_completion):
        """Test that the default timeout is passed to litellm completion calls."""
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        # Create a proper mock response
        mock_response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(content="Test response", role="assistant"),
                )
            ],
            created=1234567890,
            model="gpt-4o-mini",
            object="chat.completion",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_completion.return_value = mock_response

        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test_key"),
            usage_id="test-llm",
        )

        messages = [Message(role="user", content=[TextContent(text="Hello")])]
        llm.completion(messages=messages)

        # Verify that timeout was passed to litellm
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        assert "timeout" in call_kwargs, "timeout should be passed to litellm"
        assert call_kwargs["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS, (
            f"Expected timeout of {DEFAULT_LLM_TIMEOUT_SECONDS}s to be passed "
            f"to litellm, but got {call_kwargs['timeout']}"
        )

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_custom_timeout_passed_to_litellm(self, mock_completion):
        """Test that a custom timeout is passed to litellm completion calls."""
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        mock_response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(content="Test response", role="assistant"),
                )
            ],
            created=1234567890,
            model="gpt-4o-mini",
            object="chat.completion",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_completion.return_value = mock_response

        custom_timeout = 120
        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test_key"),
            usage_id="test-llm",
            timeout=custom_timeout,
        )

        messages = [Message(role="user", content=[TextContent(text="Hello")])]
        llm.completion(messages=messages)

        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        assert call_kwargs["timeout"] == custom_timeout

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_none_timeout_passed_to_litellm(self, mock_completion):
        """Test that None timeout is passed to litellm (no timeout)."""
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        mock_response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(content="Test response", role="assistant"),
                )
            ],
            created=1234567890,
            model="gpt-4o-mini",
            object="chat.completion",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_completion.return_value = mock_response

        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test_key"),
            usage_id="test-llm",
            timeout=None,  # Explicitly set to None
        )

        messages = [Message(role="user", content=[TextContent(text="Hello")])]
        llm.completion(messages=messages)

        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        # When explicitly set to None, it should be passed as None
        assert call_kwargs["timeout"] is None
