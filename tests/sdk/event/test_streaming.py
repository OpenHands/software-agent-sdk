"""Tests for the StreamingDeltaEvent model."""

import pytest
from pydantic import ValidationError

from openhands.sdk.event import Event, StreamingDeltaEvent


@pytest.mark.parametrize(
    "kwargs, expected_content, expected_reasoning",
    [
        ({"content": "hello world"}, "hello world", None),
        ({"reasoning_content": "thinking..."}, None, "thinking..."),
        ({"content": "hi", "reasoning_content": "hmm"}, "hi", "hmm"),
        ({}, None, None),
    ],
    ids=["content-only", "reasoning-only", "both", "empty"],
)
def test_streaming_delta_event_fields(kwargs, expected_content, expected_reasoning):
    event = StreamingDeltaEvent(**kwargs)
    assert event.content == expected_content
    assert event.reasoning_content == expected_reasoning
    assert event.source == "agent"


def test_streaming_delta_event_model_dump_includes_kind():
    event = StreamingDeltaEvent(content="x")
    dumped = event.model_dump()
    assert dumped["kind"] == "StreamingDeltaEvent"
    assert dumped["content"] == "x"
    assert dumped["source"] == "agent"


def test_streaming_delta_event_json_round_trip():
    event = StreamingDeltaEvent(content="hi", reasoning_content="hmm")
    dumped = event.model_dump(mode="json")
    assert dumped["content"] == "hi"
    assert dumped["reasoning_content"] == "hmm"


def test_streaming_delta_event_is_not_an_event():
    """A delta must not be an Event, or it rides the durable bus again."""
    assert not isinstance(StreamingDeltaEvent(content="x"), Event)
    assert not issubclass(StreamingDeltaEvent, Event)


def test_streaming_delta_event_wire_frame_is_unchanged():
    """Browser clients match on kind and require id, timestamp and source."""
    event = StreamingDeltaEvent(content="hel")
    frame = event.model_dump(mode="json", exclude_none=True)

    assert set(frame) == {"id", "timestamp", "source", "content", "kind"}
    assert frame["kind"] == "StreamingDeltaEvent"
    assert frame["source"] == "agent"
    assert frame["content"] == "hel"
    assert frame["id"] == event.id
    assert frame["timestamp"] == event.timestamp


def test_streaming_delta_event_is_frozen():
    """One instance fans out to several subscribers, so it stays immutable."""
    delta = StreamingDeltaEvent(content="x")
    with pytest.raises(ValidationError):
        delta.content = "mutated"


def test_streaming_delta_event_tolerates_additive_fields():
    """Unlike Event, a delta can gain fields without breaking older clients."""
    delta = StreamingDeltaEvent.model_validate(
        {"content": "x", "item_id": "abc", "order": 3}
    )
    assert delta.content == "x"
