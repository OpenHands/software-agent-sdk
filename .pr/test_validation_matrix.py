"""Executable validation matrix for SDK #4976.

These tests intentionally live under .pr because the full Cartesian matrix is
review evidence rather than a permanent 800-case unit-test burden.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections.abc import Iterator
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from litellm import ChatCompletionMessageToolCall, ResponseFunctionToolCall
from litellm.types.llms.openai import (
    GenericEvent,
    OutputTextDeltaEvent,
    ReasoningSummaryTextDeltaEvent,
    RefusalDeltaEvent,
    ResponseAPIUsage,
    ResponseCompletedEvent,
    ResponsesAPIResponse,
    ResponsesAPIStreamEvents,
)
from litellm.types.responses.main import OutputFunctionToolCall
from litellm.types.utils import (
    Choices,
    Delta,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
    Usage,
)
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText

from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.llm.auth.credentials import OAuthCredentials
from openhands.sdk.llm.auth.openai import (
    OpenAISubscriptionAuth,
    create_subscription_llm_from_config,
)
from openhands.sdk.llm.exceptions import LLMNoResponseError
from openhands.sdk.tool.schema import Action
from openhands.sdk.tool.tool import ToolDefinition


EXECUTION_STATES = tuple(f"E{i}" for i in range(1, 9))
SUCCESS_COMPLETIONS = tuple(f"C{i}" for i in range(1, 7))
ERROR_COMPLETIONS = ("C7", "C8")
OUTPUT_STATES = ("O1", "O2", "O3")
DELTA_STATES = tuple(f"D{i}" for i in range(1, 6))

SUCCESS_CASES = tuple(
    pytest.param(e, c, o, d, id=f"RS/{e}/{c}/{o}/{d}")
    for e, c, o, d in itertools.product(
        EXECUTION_STATES, SUCCESS_COMPLETIONS, OUTPUT_STATES, DELTA_STATES
    )
)
ERROR_CASES = tuple(
    pytest.param(e, c, d, id=f"RS/{e}/{c}/{d}")
    for e, c, d in itertools.product(EXECUTION_STATES, ERROR_COMPLETIONS, DELTA_STATES)
)


def _message_item(text: str, item_id: str = "msg_selected") -> ResponseOutputMessage:
    return ResponseOutputMessage.model_construct(
        id=item_id,
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def _response(label: str, output_state: str) -> ResponsesAPIResponse:
    output = [_message_item(label)] if output_state == "O1" else []
    return ResponsesAPIResponse(
        id=f"resp_{label}",
        created_at=0,
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=ResponseAPIUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        instructions="",
        status="completed",
    )


def _completion(response: ResponsesAPIResponse) -> ResponseCompletedEvent:
    return ResponseCompletedEvent(
        type=ResponsesAPIStreamEvents.RESPONSE_COMPLETED,
        response=response,
    )


def _delta_event(state: str) -> object:
    common = {"item_id": "delta_item", "output_index": 0, "delta": state}
    if state == "D1":
        return OutputTextDeltaEvent(
            type=ResponsesAPIStreamEvents.OUTPUT_TEXT_DELTA,
            content_index=0,
            **common,
        )
    if state == "D2":
        return RefusalDeltaEvent(
            type=ResponsesAPIStreamEvents.REFUSAL_DELTA,
            content_index=0,
            **common,
        )
    if state == "D3":
        return ReasoningSummaryTextDeltaEvent(
            type=ResponsesAPIStreamEvents.REASONING_SUMMARY_TEXT_DELTA,
            summary_index=0,
            **common,
        )
    if state == "D4":
        return object()
    return None


class _SyncStream:
    def __init__(self, events: list[object], initial: object = None, has_attr=True):
        self.events = events
        self.after: object = initial
        self.has_attr = has_attr
        if has_attr:
            self.completed_response = initial

    def __iter__(self) -> Iterator[object]:
        yield from self.events
        if self.has_attr:
            self.completed_response = self.after


class _AsyncStream:
    def __init__(self, events: list[object], initial: object = None, has_attr=True):
        self.events = events
        self.after: object = initial
        self.has_attr = has_attr
        if has_attr:
            self.completed_response = initial

    async def __aiter__(self):
        for event in self.events:
            yield event
        if self.has_attr:
            self.completed_response = self.after


def _stream_case(
    execution: str,
    completion_state: str,
    output_state: str | None,
    delta_state: str,
):
    effective_output = output_state or "O3"
    responses = {
        label: _response(label, effective_output)
        for label in ("initial", "yielded", "wrapper")
    }
    completions = {label: _completion(value) for label, value in responses.items()}
    events: list[object] = [_delta_event(delta_state)]
    if output_state == "O2":
        events.append(
            GenericEvent(
                type=ResponsesAPIStreamEvents.OUTPUT_ITEM_DONE,
                item=_message_item("collected", "msg_collected"),
            )
        )

    has_attr = completion_state not in ("C1", "C7")
    initial: object = (
        completions["initial"] if completion_state in ("C5", "C6") else None
    )
    after: object = initial
    if completion_state in ("C1", "C2", "C3", "C8"):
        events.append(completions["yielded"])
    elif completion_state == "C6":
        events.append(completions["yielded"])
    if completion_state in ("C3", "C4"):
        after = completions["wrapper"]
    elif completion_state in ("C5", "C6", "C2"):
        after = None
    elif completion_state == "C8":
        after = "invalid-completion"

    stream_type = _AsyncStream if execution in ("E3", "E4", "E5") else _SyncStream
    stream = stream_type(events, initial=initial, has_attr=has_attr)
    stream.after = after
    expected_label = {
        "C1": "yielded",
        "C2": "yielded",
        "C3": "wrapper",
        "C4": "wrapper",
        "C5": "initial",
        "C6": "yielded",
    }.get(completion_state)
    return stream, responses, expected_label


async def _invoke_matrix_case(execution, completion_state, output_state, delta_state):
    stream, responses, expected_label = _stream_case(
        execution, completion_state, output_state, delta_state
    )
    received = []

    def sync_callback(chunk):
        received.append(chunk)

    async def async_callback(chunk):
        await asyncio.sleep(0)
        received.append(chunk)

    callback = None
    if execution in ("E1", "E3", "E6"):
        callback = sync_callback
    elif execution in ("E4", "E7"):
        callback = async_callback

    llm = LLM(model="gpt-4o", num_retries=0)
    messages = [Message(role="user", content=[TextContent(text="Hello")])]
    no_callback = callback is None
    force_required = patch.object(LLM, "requires_streaming", new_callable=PropertyMock)
    required = force_required.start()
    required.return_value = no_callback
    try:
        if execution in ("E1", "E2"):
            with patch("openhands.sdk.llm.llm.litellm_responses", return_value=stream):
                result = llm.responses(messages, stream=True, on_token=callback)
        else:
            with patch(
                "openhands.sdk.llm.llm.litellm_aresponses",
                new_callable=AsyncMock,
                return_value=stream,
            ):
                result = await llm.aresponses(messages, stream=True, on_token=callback)
    finally:
        force_required.stop()

    assert expected_label is not None
    assert result.raw_response is responses[expected_label]
    expected_text = (
        expected_label
        if output_state == "O1"
        else "collected"
        if output_state == "O2"
        else None
    )
    assert [part.text for part in result.message.content] == (
        [expected_text] if expected_text else []
    )
    should_emit = callback is not None and delta_state in ("D1", "D2", "D3")
    assert [chunk.choices[0].delta.content for chunk in received] == (
        [delta_state] if should_emit else []
    )
    assert [chunk.id for chunk in received] == (["delta_item"] if should_emit else [])


@pytest.mark.parametrize(
    "execution,completion_state,output_state,delta_state", SUCCESS_CASES
)
async def test_responses_stream_success_product(
    execution, completion_state, output_state, delta_state
):
    await _invoke_matrix_case(execution, completion_state, output_state, delta_state)


@pytest.mark.parametrize("execution,completion_state,delta_state", ERROR_CASES)
async def test_responses_stream_error_product(execution, completion_state, delta_state):
    expected = (
        "without a completed response" if completion_state == "C7" else "Unexpected"
    )
    with pytest.raises(LLMNoResponseError, match=expected):
        await _invoke_matrix_case(execution, completion_state, None, delta_state)


def _user_messages():
    return [Message(role="user", content=[TextContent(text="Hello")])]


def _item_done(text: str, item_id: str):
    return SimpleNamespace(
        type=ResponsesAPIStreamEvents.OUTPUT_ITEM_DONE,
        item=_message_item(text, item_id),
    )


def test_rs_x1_invalid_initial_completion_is_rejected():
    stream = _SyncStream([], initial="invalid-completion")
    llm = LLM(model="gpt-4o", num_retries=0)
    with (
        patch.object(LLM, "requires_streaming", new_callable=PropertyMock) as required,
        patch("openhands.sdk.llm.llm.litellm_responses", return_value=stream),
        pytest.raises(LLMNoResponseError, match="Unexpected completed event"),
    ):
        required.return_value = True
        llm.responses(_user_messages(), stream=True)


@pytest.mark.parametrize("caller", ("sync", "async"))
async def test_rs_x2_non_iterable_stream_is_rejected(caller):
    llm = LLM(model="gpt-4o", num_retries=0)
    with (
        patch.object(LLM, "requires_streaming", new_callable=PropertyMock) as required,
        pytest.raises(TypeError, match="Expected a response stream"),
    ):
        required.return_value = True
        if caller == "sync":
            with patch(
                "openhands.sdk.llm.llm.litellm_responses", return_value=object()
            ):
                llm.responses(_user_messages(), stream=True)
        else:
            with patch(
                "openhands.sdk.llm.llm.litellm_aresponses",
                new_callable=AsyncMock,
                return_value=object(),
            ):
                await llm.aresponses(_user_messages(), stream=True)


@pytest.mark.parametrize("caller", ("sync", "async"))
async def test_rs_x3_complete_response_bypasses_stream_processing(caller):
    response = _response("complete", "O1")
    received = []
    llm = LLM(model="gpt-4o", num_retries=0)
    if caller == "sync":
        with patch("openhands.sdk.llm.llm.litellm_responses", return_value=response):
            result = llm.responses(
                _user_messages(), stream=True, on_token=received.append
            )
    else:
        with patch(
            "openhands.sdk.llm.llm.litellm_aresponses",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await llm.aresponses(
                _user_messages(), stream=True, on_token=received.append
            )
    assert result.raw_response is response
    assert [part.text for part in result.message.content] == ["complete"]
    assert received == []


def test_rs_x4_delta_ids_track_their_output_items():
    events = [
        OutputTextDeltaEvent(
            type=ResponsesAPIStreamEvents.OUTPUT_TEXT_DELTA,
            item_id=item_id,
            output_index=0,
            content_index=0,
            delta=delta,
        )
        for item_id, delta in (
            ("item-a", "one"),
            ("item-a", "two"),
            ("item-b", "three"),
        )
    ]
    events.append(_completion(_response("done", "O1")))
    received = []
    llm = LLM(model="gpt-4o", num_retries=0)
    with patch(
        "openhands.sdk.llm.llm.litellm_responses",
        return_value=_SyncStream(events, has_attr=False),
    ):
        llm.responses(_user_messages(), stream=True, on_token=received.append)
    assert [chunk.id for chunk in received] == ["item-a", "item-a", "item-b"]
    assert [chunk.choices[0].delta.content for chunk in received] == [
        "one",
        "two",
        "three",
    ]


def test_rs_x5_multiple_output_items_preserve_event_order():
    final = _response("empty", "O3")
    events = [
        _item_done("first", "item-first"),
        _item_done("second", "item-second"),
        _completion(final),
    ]
    llm = LLM(model="gpt-4o", num_retries=0)
    with patch(
        "openhands.sdk.llm.llm.litellm_responses",
        return_value=_SyncStream(events, has_attr=False),
    ):
        result = llm.responses(_user_messages(), stream=True, on_token=lambda _: None)
    assert [item.id for item in final.output] == ["item-first", "item-second"]
    assert [part.text for part in result.message.content] == ["first\nsecond"]


class _RaisingSyncStream:
    def __iter__(self):
        yield _delta_event("D1")
        raise RuntimeError("matrix stream failure")


def test_rs_x6_mid_stream_failure_propagates_after_prior_callback():
    received = []
    llm = LLM(model="gpt-4o", num_retries=0)
    with (
        patch(
            "openhands.sdk.llm.llm.litellm_responses",
            return_value=_RaisingSyncStream(),
        ),
        pytest.raises(RuntimeError, match="matrix stream failure"),
    ):
        llm.responses(_user_messages(), stream=True, on_token=received.append)
    assert len(received) == 1
    assert received[0].choices[0].delta.content == "D1"


def test_rs_x7_callback_failure_propagates():
    events = [_delta_event("D1"), _completion(_response("done", "O1"))]
    llm = LLM(model="gpt-4o", num_retries=0)

    def failing_callback(_):
        raise RuntimeError("matrix callback failure")

    with (
        patch(
            "openhands.sdk.llm.llm.litellm_responses",
            return_value=_SyncStream(events, has_attr=False),
        ),
        pytest.raises(RuntimeError, match="matrix callback failure"),
    ):
        llm.responses(_user_messages(), stream=True, on_token=failing_callback)


class _BlockingAsyncStream:
    def __init__(self):
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield _delta_event("D1")


async def test_rs_x8_async_stream_cancellation_propagates():
    stream = _BlockingAsyncStream()
    llm = LLM(model="gpt-4o", num_retries=0)
    with patch(
        "openhands.sdk.llm.llm.litellm_aresponses",
        new_callable=AsyncMock,
        return_value=stream,
    ):
        task = asyncio.create_task(
            llm.aresponses(_user_messages(), stream=True, on_token=lambda _: None)
        )
        await asyncio.wait_for(stream.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class _BlockingSyncStream:
    def __init__(self, started: threading.Event, release: threading.Event):
        self.started = started
        self.release = release

    def __iter__(self):
        self.started.set()
        self.release.wait(timeout=5)
        yield _completion(_response("done", "O1"))


async def test_rs_x9_async_cancellation_while_sync_iterator_is_in_executor():
    started = threading.Event()
    release = threading.Event()
    stream = _BlockingSyncStream(started, release)
    llm = LLM(model="gpt-4o", num_retries=0)
    try:
        with patch(
            "openhands.sdk.llm.llm.litellm_aresponses",
            new_callable=AsyncMock,
            return_value=stream,
        ):
            task = asyncio.create_task(
                llm.aresponses(_user_messages(), stream=True, on_token=lambda _: None)
            )
            started_wait = asyncio.get_running_loop().run_in_executor(
                None, started.wait, 2
            )
            assert await started_wait
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        release.set()


CHAT_EXECUTIONS = (
    "sync-sync",
    "async-async-sync",
    "async-async-async",
    "async-sync-sync",
    "async-sync-async",
)
CHAT_SEQUENCES = ("content", "tool", "mixed")


def _chat_chunk(delta, finish_reason=None):
    return ModelResponseStream(
        id="chat-matrix",
        choices=[
            StreamingChoices(
                finish_reason=finish_reason,
                index=0,
                delta=delta,
            )
        ],
        created=1,
        model="gpt-4o",
        object="chat.completion.chunk",
    )


def _chat_chunks(sequence):
    content = _chat_chunk(Delta(content="matrix text", role="assistant"))
    tool_start = _chat_chunk(
        Delta(
            content=None,
            role="assistant",
            tool_calls=[
                {
                    "index": 0,
                    "id": "call-matrix",
                    "type": "function",
                    "function": {"name": "matrix_tool", "arguments": ""},
                }
            ],
        )
    )
    tool_arguments = _chat_chunk(
        Delta(
            content=None,
            tool_calls=[
                {
                    "index": 0,
                    "function": {"arguments": '{"value":"x"}'},
                }
            ],
        )
    )
    chunks = [content] if sequence in ("content", "mixed") else []
    if sequence in ("tool", "mixed"):
        chunks.extend((tool_start, tool_arguments))
    finish_reason = "stop" if sequence == "content" else "tool_calls"
    chunks.append(_chat_chunk(Delta(content=None), finish_reason=finish_reason))
    return chunks


class _AsyncChatStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.parametrize("execution", CHAT_EXECUTIONS)
@pytest.mark.parametrize("sequence", CHAT_SEQUENCES)
async def test_chat_streaming_product(execution, sequence):
    chunks = _chat_chunks(sequence)
    received = []

    def sync_callback(chunk):
        received.append(chunk)

    async def async_callback(chunk):
        await asyncio.sleep(0)
        received.append(chunk)

    callback = async_callback if execution.endswith("async") else sync_callback
    llm = LLM(model="gpt-4o", num_retries=0)
    if execution == "sync-sync":
        with patch(
            "openhands.sdk.llm.llm.litellm_completion", return_value=iter(chunks)
        ):
            result = llm.completion(
                _user_messages(), stream=True, on_token=sync_callback
            )
    else:
        stream = (
            _AsyncChatStream(chunks)
            if execution.startswith("async-async")
            else iter(chunks)
        )
        with patch(
            "openhands.sdk.llm.llm.litellm_acompletion",
            new_callable=AsyncMock,
            return_value=stream,
        ):
            result = await llm.acompletion(
                _user_messages(), stream=True, on_token=callback
            )
    assert received == chunks
    expected_content = ["matrix text"] if sequence in ("content", "mixed") else []
    assert [part.text for part in result.message.content] == expected_content
    if sequence in ("tool", "mixed"):
        assert result.message.tool_calls
        call = result.message.tool_calls[0]
        assert call.id == "call-matrix"
        assert call.name == "matrix_tool"
        assert call.arguments == '{"value":"x"}'
    else:
        assert not result.message.tool_calls


class _RaisingAsyncChatStream:
    async def __aiter__(self):
        yield _chat_chunks("content")[0]
        raise RuntimeError("chat stream failure")


class _RaisingSyncChatStream:
    def __iter__(self):
        yield _chat_chunks("content")[0]
        raise RuntimeError("chat stream failure")


@pytest.mark.parametrize("transport", ("sync-sync", "async-async", "async-sync"))
async def test_chat_provider_stream_failure_propagates(transport):
    received = []
    llm = LLM(model="gpt-4o", num_retries=0)
    with pytest.raises(RuntimeError, match="chat stream failure"):
        if transport == "sync-sync":
            with patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=_RaisingSyncChatStream(),
            ):
                llm.completion(_user_messages(), stream=True, on_token=received.append)
        else:
            stream = (
                _RaisingAsyncChatStream()
                if transport == "async-async"
                else _RaisingSyncChatStream()
            )
            with patch(
                "openhands.sdk.llm.llm.litellm_acompletion",
                new_callable=AsyncMock,
                return_value=stream,
            ):
                await llm.acompletion(
                    _user_messages(), stream=True, on_token=received.append
                )
    expected_callbacks = 1 if transport != "async-sync" else 0
    assert len(received) == expected_callbacks


@pytest.mark.parametrize("execution", CHAT_EXECUTIONS)
async def test_chat_callback_failure_propagates(execution):
    chunks = _chat_chunks("content")
    llm = LLM(model="gpt-4o", num_retries=0)

    def sync_callback(_):
        raise RuntimeError("chat callback failure")

    async def async_callback(_):
        raise RuntimeError("chat callback failure")

    callback = async_callback if execution.endswith("async") else sync_callback
    with pytest.raises(RuntimeError, match="chat callback failure"):
        if execution == "sync-sync":
            with patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=iter(chunks),
            ):
                llm.completion(_user_messages(), stream=True, on_token=sync_callback)
        else:
            stream = (
                _AsyncChatStream(chunks)
                if execution.startswith("async-async")
                else iter(chunks)
            )
            with patch(
                "openhands.sdk.llm.llm.litellm_acompletion",
                new_callable=AsyncMock,
                return_value=stream,
            ):
                await llm.acompletion(_user_messages(), stream=True, on_token=callback)


class _BlockingAsyncChatStream:
    def __init__(self):
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield _chat_chunks("content")[0]


class _BlockingSyncChatStream:
    def __init__(self, started, release):
        self.started = started
        self.release = release

    def __iter__(self):
        self.started.set()
        self.release.wait(timeout=5)
        yield _chat_chunks("content")[0]


@pytest.mark.parametrize("iterator", ("async", "sync"))
async def test_chat_async_cancellation_propagates(iterator):
    llm = LLM(model="gpt-4o", num_retries=0)
    release = None
    if iterator == "async":
        stream = _BlockingAsyncChatStream()
    else:
        started = threading.Event()
        release = threading.Event()
        stream = _BlockingSyncChatStream(started, release)
    try:
        with patch(
            "openhands.sdk.llm.llm.litellm_acompletion",
            new_callable=AsyncMock,
            return_value=stream,
        ):
            task = asyncio.create_task(
                llm.acompletion(_user_messages(), stream=True, on_token=lambda _: None)
            )
            if iterator == "async":
                await asyncio.wait_for(stream.started.wait(), timeout=2)
            else:
                started_wait = asyncio.get_running_loop().run_in_executor(
                    None, stream.started.wait, 2
                )
                assert await started_wait
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        if release is not None:
            release.set()


@pytest.mark.parametrize("caller", ("sync", "async"))
@pytest.mark.parametrize("route", ("RT2", "RT5", "RT6"))
async def test_chat_request_routing(caller, route):
    response = _model_response(None, None, False)
    returned = iter(_chat_chunks("content")) if route == "RT6" else response
    callback_chunks = []
    kwargs = {"stream": True} if route == "RT2" else {}
    callback = callback_chunks.append if route == "RT5" else None
    llm = LLM(model="gpt-4o", num_retries=0)
    expected_error = pytest.raises(AssertionError, match="Expected ModelResponse")
    context = expected_error if route == "RT6" else nullcontext()
    if caller == "sync":
        with patch(
            "openhands.sdk.llm.llm.litellm_completion", return_value=returned
        ) as transport:
            with context:
                result = llm.completion(_user_messages(), on_token=callback, **kwargs)
    else:
        with patch(
            "openhands.sdk.llm.llm.litellm_acompletion",
            new_callable=AsyncMock,
            return_value=returned,
        ) as transport:
            with context:
                result = await llm.acompletion(
                    _user_messages(), on_token=callback, **kwargs
                )
    assert transport.call_args.kwargs.get("stream") in (None, False)
    assert callback_chunks == []
    if route != "RT6":
        assert result.raw_response is response


@pytest.mark.parametrize("caller", ("sync", "async"))
@pytest.mark.parametrize("route", ("RT2", "RT3", "RT4", "RT5", "RT6"))
async def test_responses_request_routing(caller, route):
    response = _response("route", "O1")
    callback_chunks = []
    kwargs = {"stream": True} if route in ("RT2", "RT3", "RT4") else {}
    callback = callback_chunks.append if route in ("RT4", "RT5") else None
    requires_streaming = route == "RT3"
    if route == "RT3":
        returned = _SyncStream([_completion(response)], has_attr=False)
    elif route == "RT6":
        returned = _SyncStream([_completion(response)], has_attr=False)
    else:
        returned = response
    llm = LLM(model="gpt-4o", num_retries=0)
    expected_error = pytest.raises(
        AssertionError, match="Expected ResponsesAPIResponse"
    )
    context = expected_error if route == "RT6" else nullcontext()
    with patch.object(LLM, "requires_streaming", new_callable=PropertyMock) as required:
        required.return_value = requires_streaming
        if caller == "sync":
            with patch(
                "openhands.sdk.llm.llm.litellm_responses", return_value=returned
            ) as transport:
                with context:
                    result = llm.responses(
                        _user_messages(), on_token=callback, **kwargs
                    )
        else:
            with patch(
                "openhands.sdk.llm.llm.litellm_aresponses",
                new_callable=AsyncMock,
                return_value=returned,
            ) as transport:
                with context:
                    result = await llm.aresponses(
                        _user_messages(), on_token=callback, **kwargs
                    )
    expected_stream = True if route in ("RT3", "RT4") else None
    assert transport.call_args.kwargs.get("stream") == expected_stream
    assert callback_chunks == []
    if route != "RT6":
        assert result.raw_response is response


REPRESENTATIONS = ("dict", "attribute", "generic", "typed")
SUMMARY_STATES = ("absent", "empty", "multiple")
PRESENCE_STATES = (False, True)


def _represented(fields: dict[str, Any], representation: str):
    if representation == "dict":
        return fields
    if representation == "attribute":
        nested = {
            key: [SimpleNamespace(**item) for item in value]
            if key in ("summary", "content") and value
            else value
            for key, value in fields.items()
        }
        return SimpleNamespace(**nested)
    if representation == "generic":
        from litellm.types.utils import BaseLiteLLMOpenAIResponseObject

        return BaseLiteLLMOpenAIResponseObject(**fields)
    return fields


@pytest.mark.parametrize(
    "representation,summary_state,has_content,has_encrypted,has_status",
    [
        pytest.param(*case, id="RN/" + "/".join(map(str, case)))
        for case in itertools.product(
            REPRESENTATIONS,
            SUMMARY_STATES,
            PRESENCE_STATES,
            PRESENCE_STATES,
            PRESENCE_STATES,
        )
    ],
)
def test_reasoning_normalization_product(
    representation, summary_state, has_content, has_encrypted, has_status
):
    summary = None if summary_state == "absent" else []
    if summary_state == "multiple":
        summary = [{"text": "one"}, {"text": "two"}]
    fields = {
        "type": "reasoning",
        "id": "reasoning-id",
        "summary": summary,
        "content": [{"text": "details"}] if has_content else None,
        "encrypted_content": "cipher" if has_encrypted else None,
        "status": "completed" if has_status else None,
    }
    if representation == "typed":
        from openai.types.responses.response_reasoning_item import (
            ResponseReasoningItem,
            Summary,
        )

        item = ResponseReasoningItem(
            id="reasoning-id",
            type="reasoning",
            summary=[
                Summary(type="summary_text", text=x["text"]) for x in summary or []
            ],
            content=None,
            encrypted_content=fields["encrypted_content"],
            status=fields["status"],
        )
        expected_content = None
    else:
        item = _represented(fields, representation)
        expected_content = ["details"] if has_content else None
    result = Message.from_llm_responses_output([item]).responses_reasoning_item
    assert result is not None
    assert result.id == "reasoning-id"
    assert result.summary == (["one", "two"] if summary_state == "multiple" else [])
    assert result.content == expected_content
    assert result.encrypted_content == ("cipher" if has_encrypted else None)
    assert result.status == ("completed" if has_status else None)


@pytest.mark.parametrize("representation", ("dict", "attribute", "generic"))
@pytest.mark.parametrize("id_state", ("both", "call-only", "item-only", "neither"))
@pytest.mark.parametrize("fields_present", (False, True))
def test_generic_function_output_product(representation, id_state, fields_present):
    fields: dict[str, Any] = {"type": "function_call"}
    if id_state in ("both", "item-only"):
        fields["id"] = "item-id"
    if id_state in ("both", "call-only"):
        fields["call_id"] = "call-id"
    if fields_present:
        fields.update(name="terminal", arguments="{}")
    message = Message.from_llm_responses_output([_represented(fields, representation)])
    assert message.tool_calls
    call = message.tool_calls[0]
    assert call.id == ("call-id" if "call_id" in fields else fields.get("id", ""))
    assert call.responses_item_id == fields.get("id")
    assert call.name == ("terminal" if fields_present else "")
    assert call.arguments == ("{}" if fields_present else "")


@pytest.mark.parametrize(
    "typed_class", (ResponseFunctionToolCall, OutputFunctionToolCall)
)
@pytest.mark.parametrize("id_state", ("both", "call-only", "item-only", "neither"))
@pytest.mark.parametrize("name_present", (False, True))
def test_typed_function_output_product(typed_class, id_state, name_present):
    fields = {
        "type": "function_call",
        "name": "terminal" if name_present else "",
        "arguments": "{}",
        "call_id": "call-id" if id_state in ("both", "call-only") else None,
        "id": "item-id" if id_state in ("both", "item-only") else None,
    }
    item = typed_class.model_construct(**fields)
    should_fail = id_state == "neither" or not name_present
    if should_fail:
        with pytest.raises(ValueError):
            Message.from_llm_responses_output([item])
        return
    message = Message.from_llm_responses_output([item])
    assert message.tool_calls
    call = message.tool_calls[0]
    assert call.id == (fields["call_id"] or fields["id"])
    assert call.responses_item_id == fields["id"]


@pytest.mark.parametrize("reasoning", ("absent", "none", "empty", "value"))
@pytest.mark.parametrize("thinking", ("absent", "empty", "signed", "redacted", "mixed"))
@pytest.mark.parametrize("tools", ("none", "valid", "invalid"))
def test_chat_metadata_product(reasoning, thinking, tools):
    kwargs: dict[str, Any] = {"role": "assistant", "content": "answer"}
    if reasoning != "absent":
        kwargs["reasoning_content"] = {
            "none": None,
            "empty": "",
            "value": "reasoning",
        }[reasoning]
    if thinking != "absent":
        blocks = {
            "empty": [],
            "signed": [{"type": "thinking", "thinking": "thought", "signature": "sig"}],
            "redacted": [{"type": "redacted_thinking", "data": "secret"}],
            "mixed": [
                {"type": "thinking", "thinking": "thought", "signature": "sig"},
                {"type": "redacted_thinking", "data": "secret"},
            ],
        }[thinking]
        kwargs["thinking_blocks"] = blocks
    if tools == "valid":
        kwargs["tool_calls"] = [
            ChatCompletionMessageToolCall(
                id="tool-id",
                type="function",
                function=Function(name="terminal", arguments="{}"),
            )
        ]
    message = LiteLLMMessage(**kwargs)
    if tools == "invalid":
        message.tool_calls = [SimpleNamespace(type="other")]
        with pytest.raises(ValueError, match="none are of type"):
            Message.from_llm_chat_message(message)
        return
    result = Message.from_llm_chat_message(message)
    expected_reasoning = {"value": "reasoning", "empty": ""}.get(reasoning)
    assert result.reasoning_content == expected_reasoning
    expected_blocks = {"absent": 0, "empty": 0, "signed": 1, "redacted": 1, "mixed": 2}
    assert len(result.thinking_blocks) == expected_blocks[thinking]
    restored = Message.model_validate_json(result.model_dump_json())
    assert restored == result
    if tools == "valid":
        assert result.tool_calls and result.tool_calls[0].id == "tool-id"


class _MatrixArgs(Action):
    value: str


class _MatrixTool(ToolDefinition[_MatrixArgs, None]):
    name = "matrix_tool"

    @classmethod
    def create(cls, _conv_state=None, **_params):
        return [cls(description="Matrix tool", action_type=_MatrixArgs)]


class _Ids:
    def __init__(self, ids):
        self.ids = ids


class _Shape:
    def __init__(self, shape, ids=None):
        self.shape = shape
        if ids is not None:
            self.ids = ids


class _GetAndEncodings:
    def __init__(self, input_ids, encodings=None):
        self.input_ids = input_ids
        self.encodings = encodings

    def get(self, key):
        assert key == "input_ids"
        return self.input_ids


class _Batch:
    def __init__(self, encodings):
        self.encodings = encodings


TOKEN_SHAPES = (
    "flat",
    "empty",
    "nested",
    "shape",
    "ids",
    "mapping",
    "get",
    "encodings",
    "encoding-sequence",
    "string",
    "shape-precedence",
    "get-none-encodings",
    "unsupported",
    "string-no-encoder",
)


def _tokenized_shape(shape):
    return {
        "flat": [1, 2, 3],
        "empty": [],
        "nested": [[1, 2, 3]],
        "shape": _Shape((1, 3)),
        "ids": _Ids([1, 2, 3]),
        "mapping": {"input_ids": [1, 2, 3]},
        "get": _GetAndEncodings([1, 2, 3]),
        "encodings": _Batch([_Ids([1, 2, 3])]),
        "encoding-sequence": [_Ids([1, 2, 3])],
        "string": "rendered",
        "shape-precedence": _Shape((1, 2), ids=[1, 2, 3, 4]),
        "get-none-encodings": _GetAndEncodings(None, [_Ids([1, 2, 3])]),
        "unsupported": object(),
        "string-no-encoder": "rendered",
    }[shape]


@pytest.mark.parametrize("shape", TOKEN_SHAPES)
@pytest.mark.parametrize("source", ("direct", "mapping"))
@pytest.mark.parametrize("with_tools", (False, True))
@pytest.mark.parametrize("message_shape", ("plain", "multipart", "tool-call"))
def test_tokenizer_product(shape, source, with_tools, message_shape):
    calls = []
    tokenized = _tokenized_shape(shape)

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return tokenized

        if shape != "string-no-encoder":

            def encode(self, text):
                assert text == "rendered"
                return _Ids([1, 2, 3])

    tokenizer = Tokenizer()
    llm = LLM(model="gpt-4o", num_retries=0)
    if source == "direct":
        llm._chat_template_tokenizer = tokenizer
    else:
        llm._tokenizer = {"tokenizer": tokenizer}

    if message_shape == "plain":
        messages = [Message(role="user", content=[TextContent(text="hello")])]
    elif message_shape == "multipart":
        messages = [
            Message(
                role="user",
                content=[TextContent(text="hello "), TextContent(text="world")],
            )
        ]
    else:
        messages = [
            Message(
                role="assistant",
                content=[TextContent(text="calling")],
                tool_calls=[
                    MessageToolCall(
                        id="call-id",
                        name="matrix_tool",
                        arguments='{"value":"x"}',
                        origin="completion",
                    )
                ],
            )
        ]
    tools = list(_MatrixTool.create()) if with_tools else None
    expected = (
        17
        if shape in ("unsupported", "string-no-encoder")
        else 2
        if shape == "shape-precedence"
        else 0
        if shape == "empty"
        else 3
    )
    with patch("openhands.sdk.llm.llm.token_counter", return_value=17) as fallback:
        assert llm.get_token_count(messages, tools=tools) == expected
    assert len(calls) == 1
    template_messages, kwargs = calls[0]
    assert kwargs["tokenize"] is True
    assert kwargs["add_generation_prompt"] is True
    assert ("tools" in kwargs) is with_tools
    if message_shape == "multipart":
        assert template_messages[0]["content"] == "hello world"
    if message_shape == "tool-call":
        arguments = template_messages[0]["tool_calls"][0]["function"]["arguments"]
        assert arguments == {"value": "x"}
    if shape in ("unsupported", "string-no-encoder"):
        fallback.assert_called_once()
    else:
        fallback.assert_not_called()


@pytest.mark.parametrize("representation", REPRESENTATIONS)
@pytest.mark.parametrize(
    "content_state", ("absent", "empty", "one", "multiple", "mixed")
)
def test_text_output_product(representation, content_state):
    content = {
        "absent": None,
        "empty": [],
        "one": [{"type": "output_text", "text": "one"}],
        "multiple": [
            {"type": "output_text", "text": " one "},
            {"type": "output_text", "text": "two"},
        ],
        "mixed": [
            {"type": "output_text", "text": "kept"},
            {"type": "refusal", "text": "ignored"},
            {"type": "unknown", "text": "ignored"},
            {"type": "output_text", "text": ""},
        ],
    }[content_state]
    fields = {"type": "message", "content": content}
    if representation == "typed":
        parts = [
            ResponseOutputText(type="output_text", text=part["text"], annotations=[])
            for part in content or []
            if part["type"] == "output_text"
        ]
        item = ResponseOutputMessage.model_construct(
            id="message-id",
            type="message",
            role="assistant",
            status="completed",
            content=parts,
        )
    else:
        item = _represented(fields, representation)
    message = Message.from_llm_responses_output([item])
    expected = {
        "absent": [],
        "empty": [],
        "one": ["one"],
        "multiple": ["one \ntwo"],
        "mixed": ["kept"],
    }[content_state]
    assert [part.text for part in message.content] == expected


@pytest.mark.parametrize(
    "order", tuple(itertools.permutations(("text", "tool", "reason")))
)
@pytest.mark.parametrize("unknown_position", ("start", "middle", "end"))
def test_mixed_output_order_and_round_trip(order, unknown_position):
    items = {
        "text": {
            "type": "message",
            "content": [{"type": "output_text", "text": "answer"}],
        },
        "tool": {
            "type": "function_call",
            "id": "item-id",
            "call_id": "call-id",
            "name": "terminal",
            "arguments": "{}",
        },
        "reason": {
            "type": "reasoning",
            "id": "reason-id",
            "summary": [{"text": "summary"}],
            "encrypted_content": "cipher",
        },
    }
    output = [items[name] for name in order]
    index = {"start": 0, "middle": 1, "end": len(output)}[unknown_position]
    output.insert(index, {"type": "unknown", "value": "ignored"})
    result = Message.from_llm_responses_output(output)
    assert [part.text for part in result.content] == ["answer"]
    assert result.tool_calls and result.tool_calls[0].id == "call-id"
    assert result.tool_calls[0].responses_item_id == "item-id"
    assert result.responses_reasoning_item
    assert result.responses_reasoning_item.encrypted_content == "cipher"
    restored = Message.model_validate_json(result.model_dump_json())
    assert restored.to_responses_dict(vision_enabled=False) == result.to_responses_dict(
        vision_enabled=False
    )


def _model_response(reasoning, provider_fields, has_markup):
    content = (
        '<function=matrix_tool><parameter=value>"x"</parameter></function>'
        if has_markup
        else "plain answer"
    )
    return ModelResponse(
        id="response-id",
        model="gpt-4o",
        created=1,
        choices=[
            Choices(
                index=0,
                finish_reason="stop",
                message=LiteLLMMessage(
                    role="assistant",
                    content=content,
                    reasoning_content=reasoning,
                    provider_specific_fields=provider_fields,
                ),
            )
        ],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


@pytest.mark.parametrize("reasoning", ("absent", "none", "empty", "value"))
@pytest.mark.parametrize("provider", ("absent", "empty", "value"))
@pytest.mark.parametrize("has_markup", (False, True))
def test_non_native_metadata_product(reasoning, provider, has_markup):
    reasoning_value = {
        "absent": None,
        "none": None,
        "empty": "",
        "value": "reasoning",
    }[reasoning]
    provider_value = {
        "absent": None,
        "empty": {},
        "value": {"signature": "sig"},
    }[provider]
    llm = LLM(model="gpt-4o", native_tool_calling=False)
    tool = list(_MatrixTool.create())[0].to_openai_tool(
        add_security_risk_prediction=False
    )
    response = _model_response(reasoning_value, provider_value, has_markup)
    converted = llm.post_response_prompt_mock(
        response,
        nonfncall_msgs=[{"role": "user", "content": "run"}],
        tools=[tool],
    )
    message = converted.choices[0].message
    assert (
        message.reasoning_content
        if reasoning == "value"
        else message.get("reasoning_content") in (None, "")
    )
    assert (
        message.provider_specific_fields
        if provider == "value"
        else message.get("provider_specific_fields") in (None, {})
    )
    if has_markup:
        assert (
            message.tool_calls and message.tool_calls[0].function.name == "matrix_tool"
        )
    else:
        assert not message.tool_calls


@pytest.mark.parametrize("credentials_available", (False, True))
@pytest.mark.parametrize("auth_state", ("api-key", "restored", "serialized"))
def test_auth_product(auth_state, credentials_available):
    credentials = OAuthCredentials(
        vendor="openai",
        access_token="access",
        refresh_token="refresh",
        expires_at=4_000_000_000_000,
    )
    if auth_state == "api-key":
        llm = LLM(model="gpt-4o", api_key="key", usage_id="matrix")
    elif auth_state == "restored":
        llm = OpenAISubscriptionAuth().create_llm(
            model="gpt-5.6-sol", credentials=credentials, usage_id="matrix"
        )
    else:
        llm = LLM(
            model="openai/gpt-5.6-sol",
            auth_type="subscription",
            subscription_vendor="openai",
            usage_id="matrix",
        )
    refreshed = credentials if credentials_available else None
    with patch.object(
        OpenAISubscriptionAuth, "refresh_if_needed_sync", return_value=refreshed
    ) as refresh:
        if auth_state == "serialized" and not credentials_available:
            with pytest.raises(ValueError, match="login is required"):
                create_subscription_llm_from_config(llm)
        else:
            result = create_subscription_llm_from_config(llm)
            if auth_state in ("api-key", "restored"):
                assert result is llm
            else:
                assert result.auth_type == "subscription"
                assert result.usage_id == "matrix"
                assert result._get_litellm_api_key_value() == "access"
    if auth_state in ("api-key", "restored"):
        refresh.assert_not_called()
    else:
        refresh.assert_called_once()


def test_auth_rejects_injected_unsupported_vendor():
    llm = LLM(
        model="openai/gpt-5.6-sol",
        auth_type="subscription",
        subscription_vendor="openai",
    )
    object.__setattr__(llm, "subscription_vendor", "unsupported")
    with pytest.raises(ValueError, match="Unsupported subscription vendor"):
        create_subscription_llm_from_config(llm)
