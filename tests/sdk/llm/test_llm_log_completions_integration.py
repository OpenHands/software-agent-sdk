"""Integration test for LLM log_completions feature.

This test verifies that log_completions doesn't produce Pydantic
serialization warnings when used with real LLM responses.
"""

import json
import os
import tempfile
import warnings
from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent

# Import common test utilities
from tests.conftest import create_mock_litellm_response


def test_llm_log_completions_integration_no_warnings():
    """Test that LLM with log_completions enabled doesn't produce warnings.

    This is an end-to-end test that creates an actual LLM instance with
    log_completions enabled and verifies no serialization warnings are raised.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create LLM with log_completions enabled
        llm = LLM(
            model="gpt-4o",
            api_key=SecretStr("test-key"),
            usage_id="test-log-completions-llm",
            log_completions=True,
            log_completions_folder=temp_dir,
            num_retries=0,
        )

        # Create a realistic mock response
        mock_response = create_mock_litellm_response(
            content="This is a test response with realistic structure.",
            response_id="integration-test-id",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            finish_reason="stop",
        )

        # Mock the litellm completion call
        with patch("openhands.sdk.llm.llm.litellm_acompletion") as mock_completion:
            mock_completion.return_value = mock_response

            # Capture any warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")

                # Make a completion call
                messages = [
                    Message(
                        role="user",
                        content=[TextContent(text="Test message")],
                    )
                ]
                llm.completion(messages)

                # Check for Pydantic serialization warnings
                pydantic_warnings = [
                    warning
                    for warning in w
                    if "PydanticSerializationUnexpectedValue" in str(warning.message)
                    or "Circular reference detected" in str(warning.message)
                ]

                warning_messages = [str(pw.message) for pw in pydantic_warnings]
                assert len(pydantic_warnings) == 0, (
                    f"Got unexpected serialization warnings: {warning_messages}"
                )

        # Verify that a log file was created
        log_files = os.listdir(temp_dir)
        assert len(log_files) == 1, f"Expected 1 log file, got {len(log_files)}"

        # Verify the log file is valid JSON and contains expected data
        log_path = os.path.join(temp_dir, log_files[0])
        with open(log_path) as f:
            log_data = json.loads(f.read())

        assert "response" in log_data
        assert "cost" in log_data
        assert "timestamp" in log_data
        assert "latency_sec" in log_data


def test_llm_log_completions_with_tool_calls():
    """Test log_completions with tool calls in the response.

    Tool calls add additional complexity to the response structure,
    so we want to ensure they serialize correctly too.
    """
    from litellm.types.utils import (
        ChatCompletionMessageToolCall,
        Choices,
        Function,
        Message as LiteLLMMessage,
        ModelResponse,
        Usage,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create LLM with log_completions enabled
        llm = LLM(
            model="gpt-4o",
            api_key=SecretStr("test-key"),
            usage_id="test-tool-calls-llm",
            log_completions=True,
            log_completions_folder=temp_dir,
            num_retries=0,
        )

        # Create a response with tool calls
        tool_call = ChatCompletionMessageToolCall(
            id="call_1",
            function=Function(name="test_function", arguments='{"param": "value"}'),
            type="function",
        )
        message = LiteLLMMessage(
            role="assistant",
            content=None,
            tool_calls=[tool_call],
        )
        choice = Choices(
            finish_reason="tool_calls",
            index=0,
            message=message,
        )
        usage = Usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        mock_response = ModelResponse(
            id="tool-call-test-id",
            choices=[choice],
            created=1234567890,
            model="gpt-4o",
            object="chat.completion",
            usage=usage,
        )

        # Mock the litellm completion call
        with patch("openhands.sdk.llm.llm.litellm_acompletion") as mock_completion:
            mock_completion.return_value = mock_response

            # Capture any warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")

                # Make a completion call
                messages = [
                    Message(
                        role="user",
                        content=[TextContent(text="Call a tool")],
                    )
                ]
                llm.completion(messages)

                # Check for Pydantic serialization warnings
                pydantic_warnings = [
                    warning
                    for warning in w
                    if "PydanticSerializationUnexpectedValue" in str(warning.message)
                    or "Circular reference detected" in str(warning.message)
                ]

                warning_messages = [str(pw.message) for pw in pydantic_warnings]
                assert len(pydantic_warnings) == 0, (
                    f"Got unexpected serialization warnings: {warning_messages}"
                )

        # Verify that a log file was created
        log_files = os.listdir(temp_dir)
        assert len(log_files) == 1

        # Verify the log contains tool call information
        log_path = os.path.join(temp_dir, log_files[0])
        with open(log_path) as f:
            log_data = json.loads(f.read())

        assert "response" in log_data
        assert log_data["response"]["choices"][0]["message"]["tool_calls"] is not None
