from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Protocol, runtime_checkable

from litellm.types.llms.openai import (
    OutputTextDeltaEvent,
    ReasoningSummaryTextDeltaEvent,
    RefusalDeltaEvent,
    ResponseCompletedEvent,
)

from openhands.sdk.llm.exceptions import LLMNoResponseError


@runtime_checkable
class OutputItemEvent(Protocol):
    @property
    def type(self) -> str: ...

    @property
    def item(self) -> object: ...


@runtime_checkable
class _CompletionSource(Protocol):
    @property
    def completed_response(self) -> object: ...


ResponseStreamEvent = (
    ResponseCompletedEvent
    | OutputTextDeltaEvent
    | RefusalDeltaEvent
    | ReasoningSummaryTextDeltaEvent
    | OutputItemEvent
)


def completed_response(
    stream: object, observed: ResponseCompletedEvent | None = None
) -> ResponseCompletedEvent | None:
    if isinstance(stream, _CompletionSource):
        completion = stream.completed_response
        if completion is not None:
            if not isinstance(completion, ResponseCompletedEvent):
                raise LLMNoResponseError(
                    f"Unexpected completed event: {type(completion)}"
                )
            return completion
    return observed


def _response_event(event: object) -> ResponseStreamEvent | None:
    if isinstance(
        event,
        (
            ResponseCompletedEvent,
            OutputTextDeltaEvent,
            RefusalDeltaEvent,
            ReasoningSummaryTextDeltaEvent,
            OutputItemEvent,
        ),
    ):
        return event
    return None


def response_events(stream: Iterable[object]) -> Iterator[ResponseStreamEvent]:
    for event in stream:
        normalized = _response_event(event)
        if normalized is not None:
            yield normalized


async def async_response_events(
    stream: AsyncIterable[object],
) -> AsyncIterator[ResponseStreamEvent]:
    async for event in stream:
        normalized = _response_event(event)
        if normalized is not None:
            yield normalized
