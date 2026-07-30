from pydantic import Field, model_validator
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.error_classification import (
    ErrorClassification,
    classify_error_code,
)


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
    classification: ErrorClassification | None = Field(
        default=None,
        description="Safe structured error semantics for API consumers.",
    )

    @model_validator(mode="after")
    def classify(self) -> "ConversationErrorEvent":
        if self.classification is None:
            object.__setattr__(self, "classification", classify_error_code(self.code))
        return self

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
