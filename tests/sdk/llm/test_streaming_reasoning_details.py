"""Tests for merging OpenRouter's reasoning_details across streamed chunks."""

from typing import Any, cast

from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

from openhands.sdk.llm.utils.streaming_reasoning_details import (
    merge_streamed_reasoning_details,
)


def _chunk(delta: Delta, *, index: int = 0) -> ModelResponseStream:
    return ModelResponseStream(
        id="chatcmpl-test",
        choices=[StreamingChoices(finish_reason=None, index=index, delta=delta)],
        created=1234567890,
        model="openrouter/anthropic/claude-3.5-sonnet",
        object="chat.completion.chunk",
    )


def test_no_chunks_carry_reasoning_details_returns_none():
    chunks = [_chunk(Delta(role="assistant", content="hi"))]
    assert merge_streamed_reasoning_details(chunks) is None


def test_single_chunk_reasoning_details_returned_verbatim():
    block = {
        "type": "reasoning.encrypted",
        "data": "opaque-test",
        "format": "anthropic-claude-v1",
        "index": 0,
    }
    chunks = [_chunk(Delta(role="assistant", content=None, reasoning_details=[block]))]
    assert merge_streamed_reasoning_details(chunks) == [block]


def test_identical_consecutive_fragments_are_appended_not_deduped():
    """Regression: repeating a fragment's exact text is valid streaming.

    A provider may legitimately split a string into fragments that repeat
    the same characters (e.g. "ha" then "ha" -> "haha", or a repeating
    base64 run). This must never be treated as a duplicate "resend the
    total" and dropped — every fragment for a given ``index`` is appended
    in arrival order, unconditionally.
    """
    chunks = [
        _chunk(
            Delta(
                role="assistant",
                content=None,
                reasoning_details=[
                    {"type": "reasoning.text", "text": "ha", "index": 0}
                ],
            )
        ),
        _chunk(
            Delta(
                content=None,
                reasoning_details=[
                    {"type": "reasoning.text", "text": "ha", "index": 0}
                ],
            )
        ),
    ]
    result = merge_streamed_reasoning_details(chunks)
    assert result == [{"type": "reasoning.text", "text": "haha", "index": 0}]


def test_multiple_fragments_join_by_index_in_arrival_order():
    chunks = [
        _chunk(
            Delta(
                role="assistant",
                content=None,
                reasoning_details=[
                    {"type": "reasoning.encrypted", "data": "AAA", "index": 0}
                ],
            )
        ),
        _chunk(
            Delta(
                content=None,
                reasoning_details=[
                    {"type": "reasoning.encrypted", "data": "BBB", "index": 0}
                ],
            )
        ),
        _chunk(
            Delta(
                content=None,
                reasoning_details=[
                    {"type": "reasoning.encrypted", "data": "CCC", "index": 0}
                ],
            )
        ),
    ]
    result = merge_streamed_reasoning_details(chunks)
    assert result == [{"type": "reasoning.encrypted", "data": "AAABBBCCC", "index": 0}]


def test_multiple_blocks_by_distinct_index_stay_independent():
    chunks = [
        _chunk(
            Delta(
                role="assistant",
                content=None,
                reasoning_details=[
                    {"type": "reasoning.text", "text": "first", "index": 0},
                    {"type": "reasoning.text", "text": "second", "index": 1},
                ],
            )
        ),
        _chunk(
            Delta(
                content=None,
                reasoning_details=[
                    {"type": "reasoning.text", "text": "-more", "index": 0},
                ],
            )
        ),
    ]
    result = merge_streamed_reasoning_details(chunks)
    assert result == [
        {"type": "reasoning.text", "text": "first-more", "index": 0},
        {"type": "reasoning.text", "text": "second", "index": 1},
    ]


def test_choices_from_other_indices_are_ignored():
    """Only the requested choice_index's deltas are merged (default 0)."""
    chunks = [
        _chunk(
            Delta(
                role="assistant",
                content=None,
                reasoning_details=[
                    {"type": "reasoning.text", "text": "no", "index": 0}
                ],
            ),
            index=1,
        )
    ]
    assert merge_streamed_reasoning_details(chunks, choice_index=0) is None


def test_delta_as_plain_dict_is_tolerated():
    """Some aggregation paths hand back plain dict deltas, not Delta models."""
    block = {"type": "reasoning.text", "text": "hi", "index": 0}
    chunk = ModelResponseStream(
        id="chatcmpl-test",
        choices=[
            StreamingChoices(
                finish_reason=None,
                index=0,
                delta=cast(
                    Any,
                    {
                        "role": "assistant",
                        "content": None,
                        "reasoning_details": [block],
                    },
                ),
            )
        ],
        created=1234567890,
        model="openrouter/anthropic/claude-3.5-sonnet",
        object="chat.completion.chunk",
    )
    assert merge_streamed_reasoning_details([chunk]) == [block]
