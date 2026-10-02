"""Associate tool execution with its originating event."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.event.types import EventID, ToolCallID


class ToolInvocation(BaseModel):
    """The event and LLM tool call responsible for one tool execution."""

    model_config = ConfigDict(frozen=True)

    action_id: EventID = Field(description="Originating action event ID.")
    tool_call_id: ToolCallID = Field(description="Originating LLM tool call ID.")


_current_invocation: ContextVar[ToolInvocation | None] = ContextVar(
    "tool_invocation", default=None
)


def get_current_tool_invocation() -> ToolInvocation | None:
    """Return the active invocation, or None outside scoped tool execution."""
    return _current_invocation.get()


@contextmanager
def tool_invocation_context(
    invocation: ToolInvocation,
) -> Iterator[None]:
    """Scope an invocation to the current execution context.

    Nested scopes restore the previous invocation even when execution raises.
    Concurrent tool executions must not share mutable invocation state.
    """
    token = _current_invocation.set(invocation)
    try:
        yield
    finally:
        _current_invocation.reset(token)
