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
    conversations. When `dynamic_context` is provided, it will be sent as a
    separate user message rather than being included in the system message.
    This enables better prompt caching across conversations, as the static
    system prompt can be cached and reused while the dynamic context varies.

    Attributes:
        system_prompt: The static system prompt text (cacheable across conversations)
        tools: List of available tools
        dynamic_context: Optional per-conversation context (hosts, repo info, etc.)
            When provided, this is converted to a user message instead of being
            appended to the system message, enabling cross-conversation cache sharing.
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
            "secrets). When provided, this is sent as a separate user message to "
            "enable cross-conversation prompt caching."
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
        """Convert to LLM message format.

        Returns only the static system prompt. The dynamic_context is handled
        separately by to_llm_messages() to enable proper prompt caching.
        """
        return Message(role="system", content=[self.system_prompt])

    def to_llm_messages(self) -> list[Message]:
        """Convert to LLM message format, potentially returning multiple messages.

        When dynamic_context is provided, returns two messages:
        1. System message with static prompt (cacheable)
        2. User message with dynamic context (not cached)

        This structure enables cross-conversation prompt caching by keeping the
        static system prompt separate from per-conversation dynamic content.

        Returns:
            List of Message objects. Contains 1 message if no dynamic_context,
            or 2 messages if dynamic_context is provided.
        """
        messages = [Message(role="system", content=[self.system_prompt])]
        if self.dynamic_context:
            messages.append(Message(role="user", content=[self.dynamic_context]))
        return messages

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
