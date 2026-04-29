"""Tests for the StreamingDeltaEvent model."""

import pytest

from openhands.sdk.event import StreamingDeltaEvent


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
