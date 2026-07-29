from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


class LLMRetryEvent(Event):
    """A retriable LLM failure surfaced to conversation clients."""

    source: SourceType = "environment"
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    error_type: str
    detail: str

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append(
            f"LLM request failed; retrying ({self.attempt}/{self.max_attempts})\n",
            style="yellow bold",
        )
        text.append(f"{self.error_type}: {self.detail}", style="yellow")
        return text
