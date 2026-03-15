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
    """Event indicating that the agent execution was interrupted.

    Unlike PauseEvent, InterruptEvent indicates that an in-flight LLM call
    was cancelled. This provides immediate interruption rather than waiting
    for the current step to complete.
    """

    source: SourceType = "user"
    reason: str = "User requested interrupt"

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this interrupt event."""
        content = Text()
        content.append("Conversation Interrupted", style="bold red")
        if self.reason != "User requested interrupt":
            content.append(f" - {self.reason}", style="dim")
        return content

    def __str__(self) -> str:
        """Plain text string representation for InterruptEvent."""
        return f"{self.__class__.__name__} ({self.source}): {self.reason}"
