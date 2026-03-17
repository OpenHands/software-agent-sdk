"""ACPToolCallEvent — surfaces ACP tool call trajectories as OpenHands events."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


_MAX_DISPLAY_CHARS = 500


class ACPToolCallEvent(Event):
    """Event representing a tool call executed by an ACP server.

    Captures the tool name, inputs, outputs, and status from ACP
    ``ToolCallStart`` / ``ToolCallProgress`` notifications so they can
    be surfaced in the OpenHands event stream and visualizer.

    This is *not* an ``LLMConvertibleEvent`` — ACP tool calls do not
    participate in LLM message conversion.
    """

    source: SourceType = "agent"
    tool_call_id: str
    title: str
    status: str | None = None
    tool_kind: str | None = None
    raw_input: Any | None = None
    raw_output: Any | None = None
    is_error: bool = False

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this tool call event."""
        content = Text()
        content.append(self.title, style="bold")

        # Kind / status metadata line
        meta_parts: list[str] = []
        if self.tool_kind:
            meta_parts.append(f"kind={self.tool_kind}")
        if self.status:
            meta_parts.append(f"status={self.status}")
        if meta_parts:
            content.append(f"\n{' | '.join(meta_parts)}", style="dim")

        # Input (skip None and empty containers like {})
        if self.raw_input:
            input_str = str(self.raw_input)
            if len(input_str) > _MAX_DISPLAY_CHARS:
                input_str = input_str[:_MAX_DISPLAY_CHARS] + "..."
            content.append("\nInput: ", style="bold")
            content.append(input_str)

        # Output (skip None and empty containers)
        if self.raw_output:
            output_str = str(self.raw_output)
            if len(output_str) > _MAX_DISPLAY_CHARS:
                output_str = output_str[:_MAX_DISPLAY_CHARS] + "..."
            content.append("\nOutput: ", style="bold")
            content.append(output_str)

        return content

    def __str__(self) -> str:
        parts = [f"{self.__class__.__name__} ({self.source}): {self.title}"]
        if self.status:
            parts.append(f"[{self.status}]")
        if self.tool_kind:
            parts.append(f"({self.tool_kind})")
        return " ".join(parts)
