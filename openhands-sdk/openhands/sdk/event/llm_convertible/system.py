import json

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import N_CHAR_PREVIEW, LLMConvertibleEvent
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.tool import ToolDefinition


class SystemPromptEvent(LLMConvertibleEvent):
    """System prompt added by the agent.

    The system prompt can optionally include dynamic context that varies between
    conversations. When ``dynamic_context`` is provided, it is included as a
    second content block in the same system message. Cache markers are NOT
    applied here - they are applied by ``LLM._apply_prompt_caching()`` when
    caching is enabled, ensuring provider-specific cache control is only added
    when appropriate.

    Attributes:
        system_prompt: The static system prompt text (cacheable across conversations)
        tools: List of available tools
        dynamic_context: Optional per-conversation context (hosts, repo info, etc.)
            Sent as a second TextContent block inside the system message.
    """

    source: SourceType = "agent"
    system_prompt: TextContent = Field(..., description="The system prompt text")
    tools: list[ToolDefinition] = Field(
        ..., description="List of tools as ToolDefinition objects"
    )
    dynamic_context: TextContent | None = Field(
        default=None,
        description=(
            "Optional dynamic per-conversation context (runtime info, repo context, "
            "secrets). When provided, this is included as a second content block in "
            "the system message (not cached)."
        ),
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this system prompt event."""
        content = Text()
        content.append("System Prompt:\n", style="bold")
        content.append(self.system_prompt.text)
        if self.dynamic_context:
            content.append("\n\nDynamic Context:\n", style="bold italic")
            context_preview = self.dynamic_context.text[:500]
            if len(self.dynamic_context.text) > 500:
                context_preview += "..."
            content.append(context_preview)
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
        """Convert to a single system LLM message.

        When ``dynamic_context`` is present the message contains two content
        blocks: the static prompt followed by the dynamic context. Cache markers
        are NOT applied here - they are applied by ``LLM._apply_prompt_caching()``
        when caching is enabled, which marks the static block (index 0) and leaves
        the dynamic block (index 1) unmarked for cross-conversation cache sharing.
        """
        if self.dynamic_context:
            return Message(
                role="system", content=[self.system_prompt, self.dynamic_context]
            )
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
        context_info = ""
        if self.dynamic_context:
            context_info = (
                f"\n  Dynamic Context: {len(self.dynamic_context.text)} chars"
            )
        return (
            f"{base_str}\n  System: {prompt_preview}\n  "
            f"Tools: {tool_count} available{context_info}"
        )
