from typing import Any

from pydantic import Field

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.types import EventID, ToolCallID
from openhands.sdk.llm import Message, TextContent


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
        # Safety net only: a user-role placeholder breaks tool_call/response
        # pairing if this event was originally an Action or Observation. Callers
        # are expected to condense UnknownEvents away before the next LLM turn.
        return Message(
            role="user",
            content=[
                TextContent(text=f"[Unloadable event '{self.original_kind}' omitted.]")
            ],
        )
