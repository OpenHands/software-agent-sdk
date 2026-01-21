import json
from enum import Enum

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import N_CHAR_PREVIEW, LLMConvertibleEvent
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.tool import ToolDefinition


class SystemPromptUpdateReason(str, Enum):
    """Reason for a SystemPromptUpdateEvent."""

    TOOLS_CHANGED = "tools_changed"


class SystemPromptEvent(LLMConvertibleEvent):
    """System prompt added by the agent."""

    source: SourceType = "agent"
    system_prompt: TextContent = Field(..., description="The system prompt text")
    tools: list[ToolDefinition] = Field(
        ..., description="List of tools as ToolDefinition objects"
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this system prompt event."""
        content = Text()
        content.append("System Prompt:\n", style="bold")
        content.append(self.system_prompt.text)
        content.append(f"\n\nTools Available: {len(self.tools)}")
        for tool in self.tools:
            # Use ToolDefinition properties directly
            description = tool.description.split("\n")[0][:100]
            if len(description) < len(tool.description):
                description += "..."

            content.append(f"\n  - {tool.name}: {description}\n")

            # Get parameters from the action type schema
            try:
                params_dict = tool.action_type.to_mcp_schema()
                params_str = json.dumps(params_dict)
                if len(params_str) > 200:
                    params_str = params_str[:197] + "..."
                content.append(f"  Parameters: {params_str}")
            except Exception:
                content.append("  Parameters: <unavailable>")
        return content

    def to_llm_message(self) -> Message:
        return Message(role="system", content=[self.system_prompt])

    def __str__(self) -> str:
        """Plain text string representation for SystemPromptEvent."""
        base_str = f"{self.__class__.__name__} ({self.source})"
        prompt_preview = (
            self.system_prompt.text[:N_CHAR_PREVIEW] + "..."
            if len(self.system_prompt.text) > N_CHAR_PREVIEW
            else self.system_prompt.text
        )
        tool_count = len(self.tools)
        return (
            f"{base_str}\n  System: {prompt_preview}\n  Tools: {tool_count} available"
        )


class SystemPromptUpdateEvent(LLMConvertibleEvent):
    """System prompt update emitted when agent config changes on conversation restore.

    This event is appended to the event log when a conversation is restored with a
    different agent configuration (e.g., different tools or system prompt). It allows
    the event log to accurately reflect what the LLM actually sees, maintaining the
    invariant that the persisted event log is the source of truth.

    When converting events to LLM messages, the latest SystemPromptEvent or
    SystemPromptUpdateEvent determines the system message content. Earlier system
    prompt events are excluded from the message list.
    """

    source: SourceType = "agent"
    system_prompt: TextContent = Field(
        ..., description="The updated system prompt text"
    )
    tools: list[ToolDefinition] = Field(
        ..., description="List of tools as ToolDefinition objects"
    )
    reason: SystemPromptUpdateReason = Field(
        ..., description="Reason for the system prompt update"
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this system prompt update event."""
        content = Text()
        content.append("System Prompt Update", style="bold yellow")
        content.append(f" (reason: {self.reason})\n")
        content.append(self.system_prompt.text)
        content.append(f"\n\nTools Available: {len(self.tools)}")
        for tool in self.tools:
            description = tool.description.split("\n")[0][:100]
            if len(description) < len(tool.description):
                description += "..."

            content.append(f"\n  - {tool.name}: {description}\n")

            try:
                params_dict = tool.action_type.to_mcp_schema()
                params_str = json.dumps(params_dict)
                if len(params_str) > 200:
                    params_str = params_str[:197] + "..."
                content.append(f"  Parameters: {params_str}")
            except Exception:
                content.append("  Parameters: <unavailable>")
        return content

    def to_llm_message(self) -> Message:
        return Message(role="system", content=[self.system_prompt])

    def __str__(self) -> str:
        """Plain text string representation for SystemPromptUpdateEvent."""
        base_str = f"{self.__class__.__name__} ({self.source}, reason={self.reason})"
        prompt_preview = (
            self.system_prompt.text[:N_CHAR_PREVIEW] + "..."
            if len(self.system_prompt.text) > N_CHAR_PREVIEW
            else self.system_prompt.text
        )
        tool_count = len(self.tools)
        return (
            f"{base_str}\n  System: {prompt_preview}\n  Tools: {tool_count} available"
        )
