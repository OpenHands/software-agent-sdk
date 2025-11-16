from __future__ import annotations

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm.streaming import LLMStreamChunk, StreamPartKind


class StreamingDeltaEvent(Event):
    """Event emitted for each incremental LLM streaming delta."""

    source: SourceType = Field(default="agent")
    stream_chunk: LLMStreamChunk

    @property
    def part_kind(self) -> StreamPartKind:
        return self.stream_chunk.part_kind

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append(f"Part: {self.stream_chunk.part_kind}\n", style="bold")

        if self.stream_chunk.text_delta:
            content.append(self.stream_chunk.text_delta)
        elif self.stream_chunk.arguments_delta:
            content.append(self.stream_chunk.arguments_delta)
        else:
            content.append("[no streaming content]")

        return content
