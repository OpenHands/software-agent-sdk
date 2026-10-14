from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Protocol, runtime_checkable

from litellm.types.llms.base import BaseLiteLLMOpenAIResponseObject
from litellm.types.llms.openai import (
    OutputTextDeltaEvent,
    ReasoningSummaryTextDeltaEvent,
    RefusalDeltaEvent,
    ResponseCompletedEvent,
    ResponsesAPIStreamEvents,
)
from pydantic import BaseModel, ConfigDict, ValidationError

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


class _GenericOutputItemEvent(BaseModel):
    # Pydantic extras are exposed dynamically, so runtime Protocol checks
    # cannot see them on Python 3.12+. Project only the fields we consume.
    model_config = ConfigDict(from_attributes=True)

    type: str = ""
    item: object = None


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
    if isinstance(event, BaseLiteLLMOpenAIResponseObject):
        try:
            normalized = _GenericOutputItemEvent.model_validate(event)
        except ValidationError:
            return None
        if normalized.type == ResponsesAPIStreamEvents.OUTPUT_ITEM_DONE:
            return normalized
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
