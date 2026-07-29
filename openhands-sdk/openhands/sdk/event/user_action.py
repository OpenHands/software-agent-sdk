from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


class PauseEvent(Event):
    """Event indicating that the agent execution was paused by user request."""

    source: SourceType = "user"

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this pause event."""
        content = Text()
        content.append("Conversation Paused", style="bold")
        return content

    def __str__(self) -> str:
        """Plain text string representation for PauseEvent."""
        return f"{self.__class__.__name__} ({self.source}): Agent execution paused"


class InterruptEvent(Event):
    """Event indicating the agent was interrupted mid-operation.

    Unlike :class:`PauseEvent` which takes effect between agent steps,
    an interrupt cancels the in-flight LLM call immediately.  The
    conversation state is set to PAUSED so it can be resumed later.
    """

    source: SourceType = "user"

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Conversation Interrupted", style="bold red")
        return content

    def __str__(self) -> str:
        return f"{self.__class__.__name__} ({self.source}): Agent execution interrupted"
