from unittest.mock import AsyncMock, patch

import pytest
from litellm.responses.streaming_iterator import (
    ResponsesAPIStreamingIterator,
    SyncResponsesAPIStreamingIterator,
)
from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError


def create_mock_response(
    content: str = "ok", response_id: str = "r-1"
) -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1,
        model="gpt-4o",
        object="chat.completion",
        system_fingerprint="t",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def create_empty_choices_response(response_id: str = "empty-1") -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[],  # triggers LLMNoResponseError inside retry boundary
        created=1,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
    )


@pytest.fixture
def base_llm() -> LLM:
    return LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        temperature=0.0,  # Explicitly set to test temperature bump behavior
    )


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_no_response_retries_then_succeeds(mock_completion, base_llm: LLM) -> None:
    mock_completion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_mock_response("success"),
    ]

    resp = base_llm.completion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert resp.message is not None
    assert mock_completion.call_count == 2  # initial + 1 retry


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_no_response_exhausts_retries_bubbles_llm_no_response(
    mock_completion, base_llm: LLM
) -> None:
    # Always return empty choices -> keeps raising LLMNoResponseError inside retry
    mock_completion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_empty_choices_response("empty-2"),
    ]

    with pytest.raises(LLMNoResponseError):
        base_llm.completion(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    # Tenacity runs function num_retries times total
    assert mock_completion.call_count == base_llm.num_retries


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_no_response_retry_bumps_temperature(mock_completion, base_llm: LLM) -> None:
    # Ensure we start at 0.0 to trigger bump to 1.0 on retry
    assert base_llm.temperature == 0.0

    mock_completion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_mock_response("ok"),
    ]

    base_llm.completion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    # Verify that on the second call, temperature was bumped to 1.0 by RetryMixin
    assert mock_completion.call_count == 2
    # Grab kwargs from the second call
    _, second_kwargs = mock_completion.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0


# ------------------------------------------------------------------
# Async acompletion tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_acompletion",
    new_callable=AsyncMock,
)
async def test_async_no_response_retry_bumps_temperature(
    mock_acompletion: AsyncMock, base_llm: LLM
) -> None:
    """Async acompletion must apply the temperature bump on retry (B2 regression)."""
    assert base_llm.temperature == 0.0

    mock_acompletion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_mock_response("ok"),
    ]

    resp = await base_llm.acompletion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert mock_acompletion.call_count == 2
    _, second_kwargs = mock_acompletion.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_acompletion",
    new_callable=AsyncMock,
)
async def test_async_no_response_retries_then_succeeds(
    mock_acompletion: AsyncMock, base_llm: LLM
) -> None:
    mock_acompletion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_mock_response("success"),
    ]

    resp = await base_llm.acompletion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert resp.message is not None
    assert mock_acompletion.call_count == 2


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_acompletion",
    new_callable=AsyncMock,
)
async def test_async_no_response_exhausts_retries(
    mock_acompletion: AsyncMock, base_llm: LLM
) -> None:
    mock_acompletion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_empty_choices_response("empty-2"),
    ]

    with pytest.raises(LLMNoResponseError):
        await base_llm.acompletion(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    assert mock_acompletion.call_count == base_llm.num_retries


# ------------------------------------------------------------------
# Async aresponses tests
# ------------------------------------------------------------------


def create_mock_responses_api_response(
    text: str = "ok",
) -> ResponsesAPIResponse:
    return ResponsesAPIResponse(
        id="resp-1",
        created_at=1,
        output=[
            ResponseOutputMessage(
                id="msg-1",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(type="output_text", text=text, annotations=[])
                ],
            )
        ],
        model="gpt-4o",
        object="response",
    )


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_aresponses",
    new_callable=AsyncMock,
)
async def test_async_aresponses_retry_bumps_temperature(
    mock_aresponses: AsyncMock, base_llm: LLM
) -> None:
    """aresponses must apply the temperature bump on retry (B2 regression)."""
    assert base_llm.temperature == 0.0

    mock_aresponses.side_effect = [
        LLMNoResponseError("empty response"),
        create_mock_responses_api_response("ok"),
    ]

    resp = await base_llm.aresponses(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert mock_aresponses.call_count == 2
    _, second_kwargs = mock_aresponses.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_aresponses",
    new_callable=AsyncMock,
)
async def test_async_aresponses_retries_then_succeeds(
    mock_aresponses: AsyncMock, base_llm: LLM
) -> None:
    mock_aresponses.side_effect = [
        LLMNoResponseError("empty response"),
        create_mock_responses_api_response("success"),
    ]

    resp = await base_llm.aresponses(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert resp.message is not None
    assert mock_aresponses.call_count == 2


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_aresponses",
    new_callable=AsyncMock,
)
async def test_async_aresponses_exhausts_retries(
    mock_aresponses: AsyncMock, base_llm: LLM
) -> None:
    mock_aresponses.side_effect = [
        LLMNoResponseError("empty-1"),
        LLMNoResponseError("empty-2"),
    ]

    with pytest.raises(LLMNoResponseError):
        await base_llm.aresponses(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    assert mock_aresponses.call_count == base_llm.num_retries


# ------------------------------------------------------------------
# Sync responses tests (exercise the shared helper extraction)
# ------------------------------------------------------------------


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_retry_bumps_temperature(mock_responses, base_llm: LLM) -> None:
    """Sync responses must apply the temperature bump on retry after the
    _prepare_responses_params / _build_responses_call_kwargs extraction."""
    assert base_llm.temperature == 0.0

    mock_responses.side_effect = [
        LLMNoResponseError("empty response"),
        create_mock_responses_api_response("ok"),
    ]

    resp = base_llm.responses(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert mock_responses.call_count == 2
    _, second_kwargs = mock_responses.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_retries_then_succeeds(mock_responses, base_llm: LLM) -> None:
    mock_responses.side_effect = [
        LLMNoResponseError("empty response"),
        create_mock_responses_api_response("success"),
    ]

    resp = base_llm.responses(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert resp.message is not None
    assert mock_responses.call_count == 2


# ------------------------------------------------------------------
# Streaming-path retry tests (stream completes without ResponseCompletedEvent)
# ------------------------------------------------------------------


class _FakeSyncStreamIterator(SyncResponsesAPIStreamingIterator):
    """Minimal sync stream iterator for testing stream-path failures.

    Mirrors :class:`_FakeAsyncStreamIterator` for the synchronous
    ``responses`` path: inherits from ``SyncResponsesAPIStreamingIterator``
    so it passes the ``isinstance`` check inside ``responses._one_attempt``,
    but skips the heavyweight parent ``__init__``.
    """

    def __init__(
        self,
        events: list,
        completed_response=None,
    ) -> None:
        # Intentionally skip parent __init__; we only need iteration
        # and the completed_response attribute.
        self._events = list(events)
        self.completed_response = completed_response

    def __iter__(self):
        return self

    def __next__(self):
        if not self._events:
            raise StopIteration
        return self._events.pop(0)


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_stream_path_retry_bumps_temperature(mock_responses) -> None:
    """Sync streaming counterpart of the aresponses stream-path test: an
    iterator that ends without a completed event raises LLMNoResponseError
    inside the retry boundary, and the retry bumps temperature 0→1.0."""
    streaming_llm = LLM(
        usage_id="test-stream-sync",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
        temperature=0.0,
        stream=True,
    )

    mock_responses.side_effect = [
        _FakeSyncStreamIterator(events=[], completed_response=None),
        create_mock_responses_api_response("ok"),
    ]

    resp = streaming_llm.responses(
        messages=[Message(role="user", content=[TextContent(text="hi")])],
        on_token=lambda _chunk: None,
    )

    assert isinstance(resp, LLMResponse)
    assert mock_responses.call_count == 2
    _, second_kwargs = mock_responses.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0


class _FakeAsyncStreamIterator(ResponsesAPIStreamingIterator):
    """Minimal async stream iterator for testing stream-path failures.

    Inherits from ``ResponsesAPIStreamingIterator`` so it passes the
    ``isinstance`` check inside ``aresponses._one_attempt``, but skips
    the heavyweight parent ``__init__``.
    """

    def __init__(
        self,
        events: list,
        completed_response=None,
    ) -> None:
        # Intentionally skip parent __init__; we only need iteration
        # and the completed_response attribute.
        self._events = list(events)
        self.completed_response = completed_response

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


@pytest.mark.asyncio
@patch(
    "openhands.sdk.llm.llm.litellm_aresponses",
    new_callable=AsyncMock,
)
async def test_async_aresponses_stream_path_retry_bumps_temperature(
    mock_aresponses: AsyncMock,
) -> None:
    """When a streaming response has no completed event, _finalize_stream_response
    raises LLMNoResponseError inside the retry boundary. On retry the temperature
    should be bumped from 0→1.0, just like the non-streaming path.
    """
    streaming_llm = LLM(
        usage_id="test-stream",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
        temperature=0.0,
        stream=True,
    )

    # First call: return an iterator that ends without a completed event
    # → _finalize_stream_response raises LLMNoResponseError
    # Second call: return a valid non-streaming response (fast path)
    mock_aresponses.side_effect = [
        _FakeAsyncStreamIterator(events=[], completed_response=None),
        create_mock_responses_api_response("ok"),
    ]

    resp = await streaming_llm.aresponses(
        messages=[Message(role="user", content=[TextContent(text="hi")])],
        on_token=lambda _chunk: None,
    )

    assert isinstance(resp, LLMResponse)
    assert mock_aresponses.call_count == 2
    _, second_kwargs = mock_aresponses.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0
