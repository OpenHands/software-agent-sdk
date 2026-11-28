"""LLM.attempt_listener sees every provider call attempt, retries included."""

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from litellm.exceptions import APIConnectionError, BadRequestError, Timeout
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import (
    Choices,
    Delta,
    Message as LiteLLMMessage,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.llm import (
    LLM,
    FallbackStrategy,
    LLMAttemptEvent,
    Message,
    TextContent,
)
from openhands.sdk.llm.exceptions import (
    LLMBadRequestError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


MESSAGES = [Message(role="user", content=[TextContent(text="Hello")])]


def _chat_response(choices: bool = True) -> ModelResponse:
    return ModelResponse(
        id="resp-1",
        choices=(
            [
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(content="Hi", role="assistant"),
                )
            ]
            if choices
            else []
        ),
        created=1234567890,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _connection_error() -> APIConnectionError:
    return APIConnectionError(
        message="connection reset", llm_provider="openai", model="gpt-4o"
    )


def _llm(events: list[LLMAttemptEvent], **kwargs) -> LLM:
    settings = {
        "model": "gpt-4o",
        "api_key": SecretStr("test-key"),
        "usage_id": "test",
        "num_retries": 2,
        "retry_min_wait": 0,
        "retry_max_wait": 0,
        "attempt_listener": events.append,
        **kwargs,
    }
    return LLM(**settings)


def _phases(events: list[LLMAttemptEvent]) -> list[tuple[str, int]]:
    return [(event.phase, event.attempt) for event in events]


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_successful_call_reports_start_and_recorded_usage(mock_completion):
    mock_completion.return_value = _chat_response()
    events: list[LLMAttemptEvent] = []
    llm = _llm(events)

    llm.completion(MESSAGES)

    assert _phases(events) == [("started", 1), ("succeeded", 1)]
    done = events[1]
    assert done.model == "gpt-4o"
    assert done.latency is not None and done.latency >= 0
    usage = done.usage
    assert usage is llm.metrics.token_usages[-1]
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (10, 5)
    assert done.error is None


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_retried_failure_is_reported_per_attempt(mock_completion):
    error = _connection_error()
    mock_completion.side_effect = [error, _chat_response()]
    events: list[LLMAttemptEvent] = []

    _llm(events).completion(MESSAGES)

    assert _phases(events) == [
        ("started", 1),
        ("failed", 1),
        ("started", 2),
        ("succeeded", 2),
    ]
    assert events[1].error is error
    assert events[1].usage is None


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_rejected_response_is_failed_with_its_recorded_usage(mock_completion):
    mock_completion.side_effect = [_chat_response(choices=False), _chat_response()]
    events: list[LLMAttemptEvent] = []
    llm = _llm(events)

    llm.completion(MESSAGES)

    assert _phases(events) == [
        ("started", 1),
        ("failed", 1),
        ("started", 2),
        ("succeeded", 2),
    ]
    rejected = events[1]
    assert rejected.usage is not None
    assert rejected.usage.prompt_tokens == 10
    assert rejected.usage is llm.metrics.token_usages[0]


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_non_retryable_failure_is_reported_and_still_raised(mock_completion):
    error = BadRequestError(message="bad request", llm_provider="openai", model="x")
    mock_completion.side_effect = error
    events: list[LLMAttemptEvent] = []

    with pytest.raises(LLMBadRequestError):
        _llm(events).completion(MESSAGES)

    assert _phases(events) == [("started", 1), ("failed", 1)]
    assert events[1].error is error


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_stream_cut_off_midway_is_failed_without_usage(mock_completion):
    error = _connection_error()

    def broken_stream() -> Iterator[ModelResponseStream]:
        yield ModelResponseStream(
            id="chunk-1",
            choices=[StreamingChoices(delta=Delta(content="Hel"), index=0)],
        )
        raise error

    mock_completion.side_effect = lambda **_: broken_stream()
    events: list[LLMAttemptEvent] = []
    received: list[ModelResponseStream] = []

    with pytest.raises(LLMServiceUnavailableError):
        _llm(events, num_retries=1).completion(
            MESSAGES, stream=True, on_token=received.append
        )

    assert len(received) == 1
    assert _phases(events) == [("started", 1), ("failed", 1)]
    assert events[1].error is error
    assert events[1].usage is None


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_acompletion", new_callable=AsyncMock)
async def test_async_retry_is_reported_per_attempt(mock_acompletion):
    mock_acompletion.side_effect = [_connection_error(), _chat_response()]
    events: list[LLMAttemptEvent] = []

    await _llm(events).acompletion(MESSAGES)

    assert _phases(events) == [
        ("started", 1),
        ("failed", 1),
        ("started", 2),
        ("succeeded", 2),
    ]


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_acompletion", new_callable=AsyncMock)
async def test_async_hard_timeout_is_failed_and_raised_as_timeout(mock_acompletion):
    async def never_answers(**_):
        await asyncio.Event().wait()

    mock_acompletion.side_effect = never_answers
    events: list[LLMAttemptEvent] = []

    with pytest.raises(LLMTimeoutError):
        await _llm(events, timeout=0, num_retries=1).acompletion(MESSAGES)

    assert _phases(events) == [("started", 1), ("failed", 1)]
    assert isinstance(events[1].error, Timeout)
    assert events[1].usage is None


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_call_reports_recorded_usage(mock_responses):
    mock_responses.return_value = ResponsesAPIResponse(
        id="resp-2",
        created_at=0,
        output=[],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=ResponseAPIUsage(input_tokens=7, output_tokens=3, total_tokens=10),
        status="completed",
    )
    events: list[LLMAttemptEvent] = []

    _llm(events).responses(MESSAGES)

    assert _phases(events) == [("started", 1), ("succeeded", 1)]
    usage = events[1].usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (7, 3)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_fallback_model_attempts_reach_the_primary_listener(mock_completion):
    def answer(**kwargs):
        if kwargs.get("model") == "gpt-4o":
            raise _connection_error()
        return _chat_response()

    mock_completion.side_effect = answer
    events: list[LLMAttemptEvent] = []
    fallback = LLM(model="fallback-model", api_key=SecretStr("k"), num_retries=0)
    primary = _llm(
        events,
        num_retries=0,
        fallback_strategy=FallbackStrategy(fallback_llms=["fallback-profile"]),
    )
    assert primary.fallback_strategy is not None
    primary.fallback_strategy._resolved = [fallback]

    primary.completion(MESSAGES)

    assert [(e.model, e.phase) for e in events] == [
        ("gpt-4o", "started"),
        ("gpt-4o", "failed"),
        ("fallback-model", "started"),
        ("fallback-model", "succeeded"),
    ]
    assert fallback.attempt_listener is None


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_listener_errors_do_not_affect_the_call(mock_completion):
    mock_completion.return_value = _chat_response()

    def broken_listener(_event: LLMAttemptEvent) -> None:
        raise RuntimeError("listener bug")

    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test",
        attempt_listener=broken_listener,
    )

    response = llm.completion(MESSAGES)

    assert response.message.content == [TextContent(text="Hi")]


def test_listener_is_not_serialized_and_survives_copy():
    events: list[LLMAttemptEvent] = []
    llm = _llm(events)

    assert "attempt_listener" not in llm.model_dump()
    assert llm.model_copy(update={"temperature": 0.5}).attempt_listener is not None
