from typing import Literal

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

    Unlike PauseEvent which waits for the current operation to complete,
    InterruptEvent indicates an immediate interruption that may have
    terminated an in-progress LLM call or tool execution.
    """

    source: SourceType = "user"
    reason: Literal["user_request", "timeout", "error"] = "user_request"
    detail: str | None = None

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this interrupt event."""
        content = Text()
        content.append("Conversation Interrupted", style="bold red")
        if self.detail:
            content.append(f"\n{self.detail}", style="dim")
        return content

    def __str__(self) -> str:
        """Plain text string representation for InterruptEvent."""
        base = f"{self.__class__.__name__} ({self.source}): Agent execution interrupted"
        if self.detail:
            base += f" - {self.detail}"
        return base
