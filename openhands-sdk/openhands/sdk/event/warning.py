from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


class WarningEvent(Event):
    """Warning event that is displayed to users but NOT sent to the LLM.

    This event is used to notify users of non-fatal issues that occurred during
    agent execution, such as critic evaluation failures. The conversation
    continues normally after a warning is emitted.
    """

    source: SourceType = "environment"
    message: str = Field(..., description="The warning message to display")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this warning event."""
        content = Text()
        content.append("⚠️ Warning: ", style="bold yellow")
        content.append(self.message)
        return content

    def __str__(self) -> str:
        """Plain text string representation for WarningEvent."""
        return f"WarningEvent: {self.message}"
