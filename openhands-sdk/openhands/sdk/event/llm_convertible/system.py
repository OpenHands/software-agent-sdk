import json

from litellm import ChatCompletionToolParam
from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import N_CHAR_PREVIEW, LLMConvertibleEvent
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import Message, TextContent


class SystemPromptEvent(LLMConvertibleEvent):
    """System prompt added by the agent."""

    source: SourceType = "agent"
    system_prompt: TextContent = Field(..., description="The system prompt text")
    tools: list[ChatCompletionToolParam] = Field(
        ..., description="List of tools in OpenAI tool format"
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this system prompt event."""
        content = Text()
        content.append("System Prompt:\n", style="bold")
        content.append(self.system_prompt.text)
        content.append(f"\n\nTools Available: {len(self.tools)}")
        for tool in self.tools:
            # Build display-only copy to avoid mutating event data
            tool_display = {
                k: (v[:27] + "..." if isinstance(v, str) and len(v) > 30 else v)
                for k, v in tool.items()
            }
            tool_fn = tool_display.get("function", None)
            if tool_fn and isinstance(tool_fn, dict):
                assert "name" in tool_fn
                assert "description" in tool_fn
                assert "parameters" in tool_fn
                params_str = json.dumps(tool_fn["parameters"])
                if len(params_str) > 200:
                    params_str = params_str[:197] + "..."
                content.append(
                    f"\n  - {tool_fn['name']}: "
                    f"{tool_fn['description'].split('\n')[0][:100]}...\n",
                )
                content.append(f"  Parameters: {params_str}")
            else:
                content.append(f"\n  - Cannot access .function for {tool_display}")
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
