from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event


class ConversationErrorEvent(Event):
    """
    Conversation-level failure that is NOT sent back to the LLM.

    This event is emitted by the conversation runtime when an unexpected
    exception bubbles up and prevents the run loop from continuing. It is
    intended for client applications (e.g., UIs) to present a top-level error
    state, and for orchestration to react. It is not an observation and it is
    not LLM-convertible.

    Differences from AgentErrorEvent:
    - Not tied to any tool_name/tool_call_id (AgentErrorEvent is a tool
      observation).
    - Typically source='environment' and the run loop moves to an ERROR state,
      while AgentErrorEvent has source='agent' and the conversation can
      continue.
    """

    code: str = Field(description="Code for the error - typically a type")
    detail: str = Field(description="Details about the error")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this conversation error event."""
        content = Text()
        content.append("Conversation Error\n", style="bold")
        content.append("Code: ", style="bold")
        content.append(self.code)
        content.append("\n\nDetail:\n", style="bold")
        content.append(self.detail)
        return content


class ConversationIterationLimitEvent(Event):
    """
    Event emitted when a conversation reaches its maximum iteration limit.

    This is a terminal event that indicates the agent has exhausted its
    allocated iterations without completing the task. It allows clients to
    distinguish between actual errors and budget exhaustion, enabling
    different retry strategies.
    """

    max_iteration_per_run: int = Field(description="The maximum allowed iterations")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this iteration limit event."""
        content = Text()
        content.append("Iteration Limit Reached\n", style="bold")
        content.append(
            f"Max Iterations: {self.max_iteration_per_run}\n", style="yellow"
        )
        return content
