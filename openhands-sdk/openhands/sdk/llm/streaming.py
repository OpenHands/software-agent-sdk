from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


StreamPartKind = Literal[
    "assistant_message",
    "reasoning_summary",
    "function_call_arguments",
    "tool_call_output",
    "refusal",
    "system",
    "status",
    "unknown",
]


@dataclass(slots=True)
class LLMStreamChunk:
    """Represents a streaming delta emitted by an LLM provider."""

    type: str
    part_kind: StreamPartKind = "unknown"
    text_delta: str | None = None
    arguments_delta: str | None = None
    output_index: int | None = None
    content_index: int | None = None
    item_id: str | None = None
    response_id: str | None = None
    is_final: bool = False
    raw_chunk: Any | None = None


TokenCallbackType = Callable[[LLMStreamChunk], None]
