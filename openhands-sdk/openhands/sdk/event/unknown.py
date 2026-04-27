from typing import Any

from pydantic import Field

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.types import EventID, ToolCallID
from openhands.sdk.llm import Message, MessageToolCall, TextContent


class UnknownEvent(LLMConvertibleEvent):
    """Placeholder for a persisted event that can't be deserialized.

    Materialized when the original event's tool schema (an ``Action`` or
    ``Observation`` subclass) is no longer registered. Preserves the IDs the
    condenser and pairing logic rely on so history stays coherent.
    """

    original_kind: str = Field(..., description="Original serialized 'kind' value.")
    original_data: dict[str, Any] = Field(
        ..., description="Original payload, retained for UI/debug; not sent to LLM."
    )
    tool_name: str | None = Field(default=None)
    tool_call_id: ToolCallID | None = Field(default=None)
    llm_response_id: EventID | None = Field(default=None)
    action_id: EventID | None = Field(default=None)

    def to_llm_message(self) -> Message:
        # Preserve tool_call/response pairing: if the original event was an
        # Observation or Action, emit a shape that keeps a surviving partner
        # event's tool_call_id answered. Callers should still condense
        # UnknownEvents away before the next LLM turn; this is a safety net.
        placeholder = f"[Unloadable event '{self.original_kind}' omitted.]"
        name = self.tool_name or "unknown"
        if self.action_id is not None and self.tool_call_id is not None:
            # Original was an Observation → tool-role reply.
            return Message(
                role="tool",
                name=name,
                tool_call_id=self.tool_call_id,
                content=[TextContent(text=placeholder)],
            )
        if self.llm_response_id is not None and self.tool_call_id is not None:
            # Original was an Action → assistant message carrying a dummy
            # tool_call so the paired Observation has something to answer.
            return Message(
                role="assistant",
                content=[],
                tool_calls=[
                    MessageToolCall(
                        id=self.tool_call_id,
                        name=name,
                        arguments="{}",
                        origin="completion",
                    )
                ],
            )
        return Message(role="user", content=[TextContent(text=placeholder)])
