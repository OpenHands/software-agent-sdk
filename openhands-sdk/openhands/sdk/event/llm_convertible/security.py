from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import N_CHAR_PREVIEW, LLMConvertibleEvent
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import Message, TextContent


class SecurityPromptEvent(LLMConvertibleEvent):
    """Security-related prompt added by the agent when security analyzer is enabled."""

    source: SourceType = "agent"
    security_prompt: TextContent = Field(
        ..., description="The security analyzer prompt text"
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this security prompt event."""
        content = Text()
        content.append("Security Prompt:\n", style="bold")
        content.append(self.security_prompt.text)
        return content

    def to_llm_message(self) -> Message:
        return Message(role="system", content=[self.security_prompt])

    def __str__(self) -> str:
        """Plain text string representation for SecurityPromptEvent."""
        base_str = f"{self.__class__.__name__} ({self.source})"
        prompt_preview = (
            self.security_prompt.text[:N_CHAR_PREVIEW] + "..."
            if len(self.security_prompt.text) > N_CHAR_PREVIEW
            else self.security_prompt.text
        )
        return f"{base_str}\n  Security: {prompt_preview}"
