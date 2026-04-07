from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event


class ConversationIterationLimitEvent(Event):
    """Conversation stopped because its per-run iteration budget was exhausted."""

    iteration: int = Field(description="Iterations consumed in the just-finished run")
    max_iterations: int = Field(description="Configured maximum iterations per run")
    detail: str = Field(description="Human-readable description of the limit event")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of the iteration-limit event."""
        content = Text()
        content.append("Iteration Limit Reached\n", style="bold")
        content.append("Iterations: ", style="bold")
        content.append(f"{self.iteration}/{self.max_iterations}")
        content.append("\n\nDetail:\n", style="bold")
        content.append(self.detail)
        return content
