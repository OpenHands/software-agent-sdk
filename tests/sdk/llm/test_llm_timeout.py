"""Tests for LLM timeout configuration."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMTimeoutError


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
        assert llm.stream_idle_timeout == DEFAULT_LLM_TIMEOUT_SECONDS

    def test_stream_idle_timeout_defaults_to_configured_timeout(self):
        llm = LLM(model="gpt-4o-mini", usage_id="test-llm", timeout=600)
        assert llm.stream_idle_timeout == 600

    def test_stream_idle_timeout_can_be_disabled(self):
        llm = LLM(
            model="gpt-4o-mini",
            usage_id="test-llm",
            timeout=600,
            stream_idle_timeout=None,
        )
        assert llm.timeout == 600
        assert llm.stream_idle_timeout is None

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
        assert call_kwargs["timeout"] is None


@asynccontextmanager
async def _hung_chat_completion_server(
    *, keep_sending: bool = False
) -> AsyncIterator[tuple[str, list[int]]]:
    attempts: list[int] = []

    async def completion(request: web.Request) -> web.StreamResponse:
        await request.json()
        attempts.append(len(attempts) + 1)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        chunk = {
            "id": f"chatcmpl-{attempts[-1]}",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi"}}],
        }
        while True:
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
            if not keep_sending:
                await asyncio.Event().wait()
            await asyncio.sleep(0.01)

    app = web.Application()
    app.router.add_post("/chat/completions", completion)
    runner = web.AppRunner(app, shutdown_timeout=0.01)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address = runner.addresses[0]
    assert isinstance(address, tuple)
    port = address[1]
    assert isinstance(port, int)
    try:
        yield f"http://127.0.0.1:{port}", attempts
    finally:
        await runner.cleanup()


def _streaming_llm(base_url: str, **kwargs) -> LLM:
    return LLM(
        model="openai/test-model",
        api_key=SecretStr("test-key"),
        base_url=base_url,
        usage_id="timeout-test",
        stream=True,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_async_stream_idle_timeout_retries_real_http_stream() -> None:
    async with _hung_chat_completion_server() as (base_url, attempts):
        llm = _streaming_llm(
            base_url,
            timeout=2,
            stream_idle_timeout=0.05,
            num_retries=2,
        )

        with pytest.raises(LLMTimeoutError, match="idle timeout"):
            await llm.acompletion(
                messages=[Message(role="user", content=[TextContent(text="Hello")])],
                on_token=lambda _: None,
            )

    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_async_hard_timeout_retries_active_real_http_stream() -> None:
    async with _hung_chat_completion_server(keep_sending=True) as (base_url, attempts):
        llm = _streaming_llm(
            base_url,
            timeout=1,
            stream_idle_timeout=2,
            num_retries=2,
        )

        with pytest.raises(LLMTimeoutError, match="hard timeout"):
            await llm.acompletion(
                messages=[Message(role="user", content=[TextContent(text="Hello")])],
                on_token=lambda _: None,
            )

    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_exhausted_stream_timeout_transitions_conversation_to_error(
    tmp_path,
) -> None:
    async with _hung_chat_completion_server() as (base_url, attempts):
        llm = _streaming_llm(
            base_url,
            timeout=2,
            stream_idle_timeout=0.05,
            num_retries=2,
        )
        conversation = LocalConversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=str(tmp_path),
            visualizer=None,
        )
        conversation.send_message("Hello")

        with pytest.raises(ConversationRunError):
            await conversation.arun()

    assert len(attempts) == 2
    assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
