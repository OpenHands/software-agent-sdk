"""Tests that malformed tool call arguments are rejected at the LLM layer
and retried automatically, preventing malformed data from ever reaching
the agent (see #2887).
"""

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)

from openhands.sdk.llm.exceptions import LLMMalformedToolArgsError
from openhands.sdk.llm.llm import _validate_tool_call_args


def _make_response(arguments: str) -> ModelResponse:
    return ModelResponse(
        id="resp-1",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call_1",
                            type="function",
                            function=Function(
                                name="file_editor",
                                arguments=arguments,
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def test_validate_tool_call_args_valid():
    """Valid JSON passes without error."""
    resp = _make_response('{"command": "view", "path": "/tmp"}')
    _validate_tool_call_args(resp)  # must not raise


def test_validate_tool_call_args_malformed():
    """Malformed JSON raises LLMMalformedToolArgsError."""
    resp = _make_response('{"command":"create","file_text":"unterminated')
    with pytest.raises(LLMMalformedToolArgsError, match="file_editor"):
        _validate_tool_call_args(resp)


def test_validate_tool_call_args_no_tool_calls():
    """Response without tool calls passes without error."""
    resp = ModelResponse(
        id="resp-2",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(role="assistant", content="Hello"),
                finish_reason="stop",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )
    _validate_tool_call_args(resp)  # must not raise


def test_malformed_tool_args_in_retry_exceptions():
    """LLMMalformedToolArgsError is in LLM_RETRY_EXCEPTIONS."""
    from openhands.sdk.llm.llm import LLM_RETRY_EXCEPTIONS

    assert LLMMalformedToolArgsError in LLM_RETRY_EXCEPTIONS
