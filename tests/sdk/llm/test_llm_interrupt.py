"""Tests for LLM cancellation and interrupt functionality."""

import asyncio
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMCancelledError


def create_mock_response(content: str = "Test response"):
    """Create a properly structured mock ModelResponse."""
    return ModelResponse(
        id="test-id",
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
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.fixture
def llm():
    """Create a test LLM instance."""
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-interrupt-llm",
        num_retries=0,  # Disable retries for predictable tests
    )


@pytest.fixture
def messages():
    """Create test messages."""
    return [
        Message(
            role="system", content=[TextContent(text="You are a helpful assistant")]
        ),
        Message(role="user", content=[TextContent(text="Hello")]),
    ]


def test_llm_has_cancel_method(llm: LLM):
    """Test that LLM has cancel method."""
    assert hasattr(llm, "cancel")
    assert callable(llm.cancel)


def test_llm_has_is_cancelled_method(llm: LLM):
    """Test that LLM has is_cancelled method."""
    assert hasattr(llm, "is_cancelled")
    assert callable(llm.is_cancelled)


def test_llm_is_cancelled_returns_false_when_no_task(llm: LLM):
    """Test is_cancelled returns False when there's no current task."""
    assert llm.is_cancelled() is False


def test_llm_cancel_does_not_raise_when_no_task(llm: LLM):
    """Test that cancel doesn't raise when there's no current task."""
    # Should not raise - calling cancel when nothing is running is OK
    llm.cancel()
    assert llm.is_cancelled() is False


def test_llm_async_loop_created_lazily(llm: LLM):
    """Test that async loop is not created until needed."""
    assert llm._async_loop is None
    assert llm._async_loop_thread is None


def test_llm_ensure_async_loop_creates_thread(llm: LLM):
    """Test that _ensure_async_loop creates and starts background thread."""
    loop = llm._ensure_async_loop()

    assert loop is not None
    assert llm._async_loop is loop
    assert llm._async_loop_thread is not None
    assert llm._async_loop_thread.is_alive()
    assert llm._async_loop_thread.daemon is True


def test_llm_ensure_async_loop_reuses_existing(llm: LLM):
    """Test that _ensure_async_loop reuses existing loop."""
    loop1 = llm._ensure_async_loop()
    loop2 = llm._ensure_async_loop()

    assert loop1 is loop2


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_llm_completion_uses_async_internally(mock_acompletion, llm: LLM, messages):
    """Test that completion uses async completion internally."""
    mock_response = create_mock_response()
    mock_acompletion.return_value = mock_response

    result = llm.completion(messages)

    assert result is not None
    mock_acompletion.assert_called_once()


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_llm_cancel_during_completion(mock_acompletion, llm: LLM, messages):
    """Test that cancel() works during a completion call."""
    # Create an event to coordinate between threads
    call_started = threading.Event()
    can_finish = threading.Event()

    async def slow_completion(*args, **kwargs):
        call_started.set()
        # Wait up to 5 seconds for signal or cancellation
        for _ in range(50):
            if can_finish.is_set():
                return create_mock_response()
            await asyncio.sleep(0.1)
        return create_mock_response()

    mock_acompletion.side_effect = slow_completion

    result_container: dict[str, Any] = {"result": None, "error": None}

    def run_completion():
        try:
            result_container["result"] = llm.completion(messages)
        except Exception as e:
            result_container["error"] = e

    # Start completion in background thread
    thread = threading.Thread(target=run_completion)
    thread.start()

    # Wait for the call to start
    call_started.wait(timeout=2)
    time.sleep(0.1)  # Small delay to ensure task is tracked

    # Cancel the call
    llm.cancel()

    # Wait for thread to finish
    thread.join(timeout=3)

    # Should have raised LLMCancelledError
    assert result_container["error"] is not None
    assert isinstance(result_container["error"], LLMCancelledError)


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_llm_cancel_is_thread_safe(mock_acompletion, messages):
    """Test that cancel() can be called from multiple threads safely."""
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-thread-safe",
        num_retries=0,
    )

    mock_acompletion.return_value = create_mock_response()

    # Call cancel from multiple threads concurrently
    threads = []
    for i in range(10):
        t = threading.Thread(target=llm.cancel)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=2)

    # Should not raise any errors


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_llm_can_be_reused_after_cancel(mock_acompletion, llm: LLM, messages):
    """Test that LLM can be used for new calls after cancellation."""
    call_count = 0
    call_started = threading.Event()

    async def slow_then_fast(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            call_started.set()
            # First call is slow
            await asyncio.sleep(10)
        # Second call returns immediately
        return create_mock_response(f"Response {call_count}")

    mock_acompletion.side_effect = slow_then_fast

    # First call - will be cancelled
    result_container: dict[str, Any] = {"error": None}

    def first_call():
        try:
            llm.completion(messages)
        except LLMCancelledError as e:
            result_container["error"] = e

    thread = threading.Thread(target=first_call)
    thread.start()
    call_started.wait(timeout=2)
    time.sleep(0.1)
    llm.cancel()
    thread.join(timeout=3)

    assert result_container["error"] is not None
    assert isinstance(result_container["error"], LLMCancelledError)

    # Reset mock for second call
    mock_acompletion.side_effect = None
    mock_acompletion.return_value = create_mock_response("Second response")

    # Second call - should work normally
    result = llm.completion(messages)
    assert result is not None
    # Check the content via the message
    assert result.message.content is not None
    assert len(result.message.content) > 0
    first_content = result.message.content[0]
    # Verify it's a TextContent and contains expected text
    assert isinstance(first_content, TextContent)
    assert "Second response" in first_content.text


def test_llm_cancelled_error_exception():
    """Test LLMCancelledError exception properties."""
    error = LLMCancelledError()
    assert str(error) == "LLM call was cancelled"
    assert error.message == "LLM call was cancelled"

    custom_error = LLMCancelledError("Custom cancellation message")
    assert str(custom_error) == "Custom cancellation message"
    assert custom_error.message == "Custom cancellation message"


def test_llm_cancelled_error_can_be_caught():
    """Test that LLMCancelledError can be caught as Exception."""
    with pytest.raises(LLMCancelledError):
        raise LLMCancelledError("test")

    # Should also be catchable as generic Exception
    try:
        raise LLMCancelledError("test")
    except Exception as e:
        assert isinstance(e, LLMCancelledError)
