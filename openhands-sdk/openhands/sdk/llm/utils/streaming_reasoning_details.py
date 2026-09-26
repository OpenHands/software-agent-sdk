"""Reconstruct OpenRouter's ``reasoning_details`` across streamed chunks.

``litellm.stream_chunk_builder`` only aggregates fields it knows about.
``reasoning_details`` is not a declared field on litellm's ``Delta``/
``Message`` types (it only ever arrives via Pydantic's ``extra="allow"``
bucket), so the builder silently drops it when reassembling a streamed
response. This module rebuilds it from the raw chunks so the SDK can patch
it back onto the built message before ``Message.from_llm_chat_message``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from litellm.types.utils import ModelResponseStream


def _reasoning_details_from_delta(delta: Any) -> list[dict[str, Any]] | None:
    """Read a chunk delta's ``reasoning_details``, tolerating dict or model."""
    if delta is None:
        return None
    if isinstance(delta, dict):
        return delta.get("reasoning_details")
    try:
        extra = delta.model_extra
    except AttributeError:
        return None
    return (extra or {}).get("reasoning_details")


def merge_streamed_reasoning_details(
    chunks: list[ModelResponseStream],
    *,
    choice_index: int = 0,
) -> list[dict[str, Any]] | None:
    """Merge ``reasoning_details`` blocks across ``chunks`` for one choice.

    Blocks are merged by their ``index`` key: every occurrence of a block
    for a given index after the first is treated as the next incremental
    fragment and its string fields (``data``, ``text``, ``summary``) are
    *appended* in arrival order - including when a fragment happens to
    repeat the same characters as the one before it (e.g. ``"ha"`` then
    ``"ha"`` -> ``"haha"``, or a repeating base64 chunk). There is no
    documented "resend the total" mode for this field, so this never drops
    a fragment on the basis of it matching prior content.
    """
    merged: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for chunk in chunks:
        for choice in chunk.choices:
            if choice.index not in (choice_index, None):
                continue
            details = _reasoning_details_from_delta(choice.delta)
            if not details:
                continue
            for block in details:
                key = block.get("index", len(order))
                existing = merged.get(key)
                if existing is None:
                    merged[key] = dict(block)
                    order.append(key)
                    continue
                for field, value in block.items():
                    if (
                        field in ("data", "text", "summary")
                        and isinstance(value, str)
                        and isinstance(existing.get(field), str)
                    ):
                        existing[field] += value
                    else:
                        existing[field] = value
    if not merged:
        return None
    return [merged[key] for key in order]
