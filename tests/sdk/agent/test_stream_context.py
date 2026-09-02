"""StreamContext mints one identity per step and always closes it.

See https://github.com/OpenHands/software-agent-sdk/issues/4682.
"""

import asyncio
import uuid

import pytest
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

from openhands.sdk.agent.stream_context import (
    StreamAborted,
    StreamContext,
    StreamDelta,
    StreamStarted,
)


def _chunk(
    content: str | None = None,
    reasoning_content: str | None = None,
    chunk_id: str = "chunk-1",
    index: int = 0,
) -> ModelResponseStream:
    delta_kwargs: dict = {"role": "assistant"}
    if content is not None:
        delta_kwargs["content"] = content
    delta = Delta(**delta_kwargs)
    if reasoning_content is not None:
        object.__setattr__(delta, "reasoning_content", reasoning_content)
    choice = StreamingChoices(delta=delta, index=index, finish_reason=None)
    return ModelResponseStream(id=chunk_id, choices=[choice], model="test-model")


def _make(
    frames: list, forwarded: list | None = None, mask=lambda text: text
) -> StreamContext:
    return StreamContext(
        item_id=str(uuid.uuid4()),
        anchor_seq=7,
        on_token=forwarded.append if forwarded is not None else None,
        on_stream=frames.append,
        mask=mask,
    )


def test_a_step_that_never_streams_opens_nothing():
    frames: list = []
    with _make(frames):
        pass
    assert frames == []


def test_first_delta_opens_the_slot_with_the_anchor():
    frames: list = []
    with _make(frames) as stream:
        stream.on_chunk(_chunk("hello"))

    started, delta, aborted = frames
    assert isinstance(started, StreamStarted)
    assert started.item_id == stream.item_id
    assert started.attempt == 1
    assert started.anchor_seq == 7
    assert isinstance(delta, StreamDelta)
    assert (delta.kind, delta.content, delta.order) == ("text", "hello", 0)
    assert (delta.chunk_id, delta.choice_index) == ("chunk-1", 0)
    # Nothing claimed the id, so the slot is retired by an abort.
    assert isinstance(aborted, StreamAborted)


def test_a_claimed_id_retires_the_slot_without_an_abort():
    frames: list = []
    with _make(frames) as stream:
        stream.on_chunk(_chunk("hello"))
        claimed = stream.claim()

    assert claimed == stream.item_id
    assert not any(isinstance(f, StreamAborted) for f in frames)


def test_the_id_is_claimable_once():
    frames: list = []
    stream = _make(frames)
    stream.on_chunk(_chunk("hi"))
    assert stream.claim() == stream.item_id
    assert stream.claim() is None


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("boom"), "RuntimeError"),
        (asyncio.CancelledError(), "cancelled"),
        (None, "no_durable_event"),
    ],
    ids=["provider-failure", "cancellation", "returned-without-a-message"],
)
def test_every_opened_slot_is_retired_exactly_once(exc, expected):
    frames: list = []
    stream = _make(frames)
    try:
        with stream:
            stream.on_chunk(_chunk("partial"))
            if exc is not None:
                raise exc
    except BaseException as raised:
        assert raised is exc

    aborts = [f for f in frames if isinstance(f, StreamAborted)]
    assert len(aborts) == 1
    assert aborts[0].reason == expected
    assert aborts[0].item_id == stream.item_id


def test_a_retry_re_streams_the_same_item_under_a_higher_attempt():
    frames: list = []
    with _make(frames) as stream:
        stream.on_chunk(_chunk("half", chunk_id="completion-a"))
        # litellm mints a new completion id per attempt.
        stream.on_chunk(_chunk("half", chunk_id="completion-b"))
        stream.on_chunk(_chunk(" again", chunk_id="completion-b"))
        stream.claim()

    starts = [f for f in frames if isinstance(f, StreamStarted)]
    deltas = [f for f in frames if isinstance(f, StreamDelta)]
    assert [f.attempt for f in starts] == [1, 2]
    assert {f.item_id for f in starts} == {stream.item_id}
    # order is monotonic within (item_id, attempt), so the retry restarts it.
    assert [(d.attempt, d.order) for d in deltas] == [(1, 0), (2, 0), (2, 1)]


def test_reasoning_and_text_are_separate_ordered_deltas():
    frames: list = []
    with _make(frames) as stream:
        stream.on_chunk(_chunk(content="answer", reasoning_content="thought"))
        stream.claim()

    deltas = [f for f in frames if isinstance(f, StreamDelta)]
    assert [(d.kind, d.content, d.order) for d in deltas] == [
        ("reasoning", "thought", 0),
        ("text", "answer", 1),
    ]


def test_deltas_are_masked_by_the_snapshot():
    frames: list = []
    with _make(
        frames, mask=lambda t: t.replace("hunter2", "<secret-hidden>")
    ) as stream:
        stream.on_chunk(_chunk("token is hunter2"))
        stream.claim()

    delta = next(f for f in frames if isinstance(f, StreamDelta))
    assert delta.content == "token is <secret-hidden>"


def test_the_raw_chunk_still_reaches_the_token_callback():
    """The CLI, the legacy socket and user callbacks must see no change."""
    frames: list = []
    forwarded: list = []
    chunk = _chunk("hello")
    with _make(frames, forwarded) as stream:
        stream.on_chunk(chunk)
        stream.on_chunk("acp bare string")
        stream.claim()

    assert forwarded == [chunk, "acp bare string"]


def test_without_a_sink_nothing_is_minted_and_chunks_still_flow():
    forwarded: list = []
    stream = StreamContext(
        item_id=str(uuid.uuid4()),
        anchor_seq=None,
        on_token=forwarded.append,
        on_stream=None,
        mask=lambda t: t,
    )
    with stream:
        stream.on_chunk(_chunk("hello"))
        # No consumer, so the durable event keeps its own id.
        assert stream.claim() is None
    assert len(forwarded) == 1
    assert stream.enabled is False


def test_a_broken_sink_does_not_fail_the_turn():
    def explode(_frame):
        raise ValueError("subscriber blew up")

    forwarded: list = []
    stream = StreamContext(
        item_id="item",
        anchor_seq=None,
        on_token=forwarded.append,
        on_stream=explode,
        mask=lambda t: t,
    )
    stream.on_chunk(_chunk("hello"))
    assert len(forwarded) == 1
