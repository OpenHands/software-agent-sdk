"""Tests for LLM completion functionality, configuration, and metrics tracking."""

from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from litellm import ChatCompletionMessageToolCall, CustomStreamWrapper
from litellm.types.utils import (
    Choices,
    Delta,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
    StreamingChoices,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.llm import (
    LLM,
    Message,
    TextContent,
)
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import ToolDefinition


def create_mock_response(content: str = "Test response", response_id: str = "test-id"):
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
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


# Helper tool classes for testing
class _ArgsBasic(Action):
    """Basic action for testing."""

    param: str


class _MockTool(ToolDefinition[_ArgsBasic, None]):
    """Mock tool for LLM completion testing."""

    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_MockTool"]:
        return [cls(description="A test tool", action_type=_ArgsBasic)]


@pytest.fixture
def default_config():
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-llm",
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_llm_completion_basic(mock_completion):
    """Test basic LLM completion functionality."""
    mock_response = create_mock_response("Test response")
    mock_completion.return_value = mock_response
    # Create LLM after the patch is applied

    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Test completion
    messages = [Message(role="user", content=[TextContent(text="Hello")])]
    response = llm.completion(messages=messages)

    # Check that response is a LLMResponse with expected properties
    assert response.raw_response == mock_response
    assert response.message.role == "assistant"
    assert isinstance(response.message.content[0], TextContent)
    assert response.message.content[0].text == "Test response"
    assert response.metrics.model_name == "gpt-4o"
    mock_completion.assert_called_once()

    # Additionally, verify the pre-check helper recognizes provider-style tools
    # (use an empty list of tools here just to exercise the path)
    cc_tools = []
    assert not llm.should_mock_tool_calls(cc_tools)


def test_llm_streaming_not_supported(default_config):
    """Test that streaming requires an on_token callback."""
    llm = default_config

    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    # Streaming without callback should raise an error
    with pytest.raises(ValueError, match="Streaming requires an on_token callback"):
        llm.completion(messages=messages, stream=True)


@patch("openhands.sdk.llm.llm.litellm_completion")
@patch("openhands.sdk.llm.llm.litellm.stream_chunk_builder")
def test_llm_completion_streaming_with_callback(mock_stream_builder, mock_completion):
    """Test that streaming with on_token callback works correctly."""

    # Create stream chunks
    chunk1 = ModelResponse(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason=None,
                index=0,
                delta=Delta(content="Hello", role="assistant"),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion.chunk",
    )

    chunk2 = ModelResponse(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason=None,
                index=0,
                delta=Delta(content=" world!", role=None),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion.chunk",
    )

    chunk3 = ModelResponse(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason="stop",
                index=0,
                delta=Delta(content=None, role=None),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion.chunk",
    )

    # Create a mock stream wrapper
    mock_stream = MagicMock(spec=CustomStreamWrapper)
    mock_stream.__iter__.return_value = iter([chunk1, chunk2, chunk3])
    mock_completion.return_value = mock_stream

    # Mock the stream builder to return a complete response
    final_response = create_mock_response("Hello world!")
    mock_stream_builder.return_value = final_response

    # Create LLM
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Track chunks received by callback
    received_chunks = []

    def on_token(chunk):
        received_chunks.append(chunk)

    messages = [Message(role="user", content=[TextContent(text="Hello")])]
    response = llm.completion(messages=messages, stream=True, on_token=on_token)

    # Verify callback was invoked for each chunk
    assert len(received_chunks) == 3
    assert received_chunks[0] == chunk1
    assert received_chunks[1] == chunk2
    assert received_chunks[2] == chunk3

    # Verify stream builder was called to assemble final response
    mock_stream_builder.assert_called_once()

    # Verify final response
    assert response.message.role == "assistant"
    assert isinstance(response.message.content[0], TextContent)
    assert response.message.content[0].text == "Hello world!"


@patch("openhands.sdk.llm.llm.litellm_completion")
@patch("openhands.sdk.llm.llm.litellm.stream_chunk_builder")
def test_llm_completion_streaming_with_tools(mock_stream_builder, mock_completion):
    """Test streaming completion with tool calls."""

    # Create stream chunks with tool call
    chunk1 = ModelResponse(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason=None,
                index=0,
                delta=Delta(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "test_tool", "arguments": ""},
                        }
                    ],
                ),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion.chunk",
    )

    chunk2 = ModelResponse(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason=None,
                index=0,
                delta=Delta(
                    content=None,
                    tool_calls=[
                        {
                            "index": 0,
                            "function": {"arguments": '{"param": "value"}'},
                        }
                    ],
                ),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion.chunk",
    )

    chunk3 = ModelResponse(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason="tool_calls",
                index=0,
                delta=Delta(content=None),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion.chunk",
    )

    # Create mock stream
    mock_stream = MagicMock(spec=CustomStreamWrapper)
    mock_stream.__iter__.return_value = iter([chunk1, chunk2, chunk3])
    mock_completion.return_value = mock_stream

    # Mock final response with tool call
    final_response = create_mock_response("I'll use the tool")
    final_response.choices[0].message.tool_calls = [  # type: ignore
        ChatCompletionMessageToolCall(
            id="call_123",
            type="function",
            function=Function(
                name="test_tool",
                arguments='{"param": "value"}',
            ),
        )
    ]
    mock_stream_builder.return_value = final_response

    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
    )

    received_chunks = []

    def on_token(chunk):
        received_chunks.append(chunk)

    messages = [Message(role="user", content=[TextContent(text="Use test_tool")])]
    tools = list(_MockTool.create())

    response = llm.completion(
        messages=messages, tools=tools, stream=True, on_token=on_token
    )

    # Verify chunks were received
    assert len(received_chunks) == 3

    # Verify final response has tool call
    assert response.message.tool_calls is not None
    assert len(response.message.tool_calls) == 1
    assert response.message.tool_calls[0].name == "test_tool"


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_llm_completion_with_tools(mock_completion):
    """Test LLM completion with tools."""
    mock_response = create_mock_response("I'll use the tool")
    mock_response.choices[0].message.tool_calls = [  # type: ignore
        ChatCompletionMessageToolCall(
            id="call_123",
            type="function",
            function=Function(
                name="test_tool",
                arguments='{"param": "value"}',
            ),
        )
    ]
    mock_completion.return_value = mock_response

    # Create LLM after the patch is applied
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Test completion with tools
    messages = [Message(role="user", content=[TextContent(text="Use the test tool")])]

    tools_list = list(_MockTool.create())

    response = llm.completion(messages=messages, tools=tools_list)

    # Check that response is a LLMResponse with expected properties
    assert response.raw_response == mock_response
    assert response.message.role == "assistant"
    assert isinstance(response.message.content[0], TextContent)
    assert response.message.content[0].text == "I'll use the tool"
    assert response.message.tool_calls is not None
    assert len(response.message.tool_calls) == 1
    assert response.message.tool_calls[0].id == "call_123"
    assert response.message.tool_calls[0].name == "test_tool"
    mock_completion.assert_called_once()


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_llm_completion_error_handling(mock_completion):
    """Test LLM completion error handling."""
    # Mock an exception
    mock_completion.side_effect = Exception("Test error")

    # Create LLM after the patch is applied
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    # Should propagate the exception
    with pytest.raises(Exception, match="Test error"):
        llm.completion(messages=messages)


def test_llm_token_counting_basic(default_config):
    """Test basic token counting functionality."""
    llm = default_config

    # Test with simple messages
    messages = [
        Message(role="user", content=[TextContent(text="Hello")]),
        Message(role="assistant", content=[TextContent(text="Hi there!")]),
    ]

    # Token counting should return a non-negative integer
    token_count = llm.get_token_count(messages)
    assert isinstance(token_count, int)
    assert token_count >= 0


def test_llm_model_info_initialization(default_config):
    """Test model info initialization."""
    llm = default_config

    # Model info initialization should complete without errors
    llm._init_model_info_and_caps()

    # Model info might be None for unknown models, which is fine
    assert llm.model_info is None or isinstance(llm.model_info, dict)


def test_llm_feature_detection(default_config):
    """Test various feature detection methods."""
    llm = default_config

    # All feature detection methods should return booleans
    assert isinstance(llm.vision_is_active(), bool)
    assert isinstance(llm.native_tool_calling, bool)
    assert isinstance(llm.is_caching_prompt_active(), bool)


def test_llm_cost_tracking(default_config):
    """Test cost tracking functionality."""
    llm = default_config

    initial_cost = llm.metrics.accumulated_cost

    # Add some cost
    llm.metrics.add_cost(1.5)

    assert llm.metrics.accumulated_cost == initial_cost + 1.5
    assert len(llm.metrics.costs) >= 1


def test_llm_latency_tracking(default_config):
    """Test latency tracking functionality."""
    llm = default_config

    initial_count = len(llm.metrics.response_latencies)

    # Add some latency
    llm.metrics.add_response_latency(0.5, "test-response")

    assert len(llm.metrics.response_latencies) == initial_count + 1
    assert llm.metrics.response_latencies[-1].latency == 0.5


def test_llm_token_usage_tracking(default_config):
    """Test token usage tracking functionality."""
    llm = default_config

    initial_count = len(llm.metrics.token_usages)

    # Add some token usage
    llm.metrics.add_token_usage(
        prompt_tokens=10,
        completion_tokens=5,
        cache_read_tokens=2,
        cache_write_tokens=1,
        context_window=4096,
        response_id="test-response",
    )

    assert len(llm.metrics.token_usages) == initial_count + 1

    # Check accumulated token usage
    accumulated = llm.metrics.accumulated_token_usage
    assert accumulated.prompt_tokens >= 10
    assert accumulated.completion_tokens >= 5


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_llm_completion_with_custom_params(mock_completion, default_config):
    """Test LLM completion with custom parameters."""
    mock_response = create_mock_response("Custom response")
    mock_completion.return_value = mock_response

    # Create config with custom parameters
    custom_config = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        temperature=0.8,
        max_output_tokens=500,
        top_p=0.9,
    )

    llm = custom_config

    messages = [
        Message(role="user", content=[TextContent(text="Hello with custom params")])
    ]
    response = llm.completion(messages=messages)

    # Check that response is a LLMResponse with expected properties
    assert response.raw_response == mock_response
    assert response.message.role == "assistant"
    assert isinstance(response.message.content[0], TextContent)
    assert response.message.content[0].text == "Custom response"
    mock_completion.assert_called_once()

    # Verify that custom parameters were used in the call
    call_kwargs = mock_completion.call_args[1]
    assert call_kwargs.get("temperature") == 0.8
    assert call_kwargs.get("max_completion_tokens") == 500
    assert call_kwargs.get("top_p") == 0.9


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_llm_completion_non_function_call_mode(mock_completion):
    """Test LLM completion with non-function call mode (prompt-based tool calling)."""
    # Create a mock response that looks like a non-function call response
    # but contains tool usage in text format
    mock_response = create_mock_response(
        "I'll help you with that.\n"
        "<function=test_tool>\n"
        "<parameter=param>test_value</parameter>\n"
        "</function>"
    )
    mock_completion.return_value = mock_response

    # Create LLM with native_tool_calling explicitly set to False
    # This forces the LLM to use prompt-based tool calling instead of native FC
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        # This is the key setting for non-function call mode
        native_tool_calling=False,
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Verify that function calling is not active
    assert not llm.native_tool_calling

    # Test completion with tools - this should trigger the non-function call path
    messages = [
        Message(
            role="user",
            content=[TextContent(text="Use the test tool with param 'test_value'")],
        )
    ]

    tools = list(_MockTool.create())

    # Verify that tools should be mocked (non-function call path)
    cc_tools = [t.to_openai_tool(add_security_risk_prediction=False) for t in tools]
    assert llm.should_mock_tool_calls(cc_tools)

    # Call completion - this should go through the prompt-based tool calling path
    response = llm.completion(messages=messages, tools=tools)

    # Verify the response
    assert response is not None
    mock_completion.assert_called_once()
    # And that post-response conversion produced a tool_call
    # Access message through LLMResponse interface
    msg = response.message
    # Guard for optional attribute: treat None as failure explicitly
    assert getattr(msg, "tool_calls", None) is not None, (
        "Expected tool_calls after post-mock"
    )
    # At this point, tool_calls should be non-None; assert explicitly
    assert msg.tool_calls is not None
    tc = msg.tool_calls[0]

    assert tc.name == "test_tool"
    # Ensure function-call markup was stripped from assistant content
    if msg.content:
        for content_item in msg.content:
            if isinstance(content_item, TextContent):
                assert "<function=" not in content_item.text

    # Verify that the call was made without native tools parameter
    # (since we're using prompt-based tool calling)
    call_kwargs = mock_completion.call_args[1]
    # In non-function call mode, tools should not be passed to the underlying LLM
    assert call_kwargs.get("tools") is None

    # Verify that the messages were modified for prompt-based tool calling
    call_messages = mock_completion.call_args[1]["messages"]
    # The messages should be different from the original due to prompt modification
    assert len(call_messages) >= len(messages)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_llm_completion_function_call_vs_non_function_call_mode(mock_completion):
    """Test the difference between function call mode and non-function call mode."""
    mock_response = create_mock_response("Test response")
    mock_completion.return_value = mock_response

    tools = list(_MockTool.create())
    messages = [Message(role="user", content=[TextContent(text="Use the test tool")])]

    # Test with native function calling enabled (default behavior for gpt-4o)
    llm_native = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        native_tool_calling=True,  # Explicitly enable native function calling
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Verify function calling is active
    assert llm_native.native_tool_calling
    # Should not mock tools when native function calling is active

    # Test with native function calling disabled
    llm_non_native = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        native_tool_calling=False,  # Explicitly disable native function calling
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Verify function calling is not active
    assert not llm_non_native.native_tool_calling

    # Call both and verify different behavior
    mock_completion.reset_mock()
    response_native = llm_native.completion(messages=messages, tools=tools)
    native_call_kwargs = mock_completion.call_args[1]

    mock_completion.reset_mock()
    response_non_native = llm_non_native.completion(messages=messages, tools=tools)
    non_native_call_kwargs = mock_completion.call_args[1]

    # Both should return LLMResponse responses
    assert response_native.raw_response == mock_response
    assert response_native.message.role == "assistant"
    assert response_non_native.raw_response == mock_response
    assert response_non_native.message.role == "assistant"

    # But the underlying calls should be different:
    # Native mode should pass tools to the LLM
    assert isinstance(native_call_kwargs.get("tools"), list)
    assert native_call_kwargs["tools"][0]["type"] == "function"
    assert native_call_kwargs["tools"][0]["function"]["name"] == "test_tool"

    # Non-native mode should not pass tools (they're handled via prompts)
    assert non_native_call_kwargs.get("tools") is None


# This file focuses on LLM completion functionality, configuration options,
# and metrics tracking for the synchronous LLM implementation
