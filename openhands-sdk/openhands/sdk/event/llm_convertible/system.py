from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import N_CHAR_PREVIEW, LLMConvertibleEvent
from openhands.sdk.event.types import SourceType
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.hooks import HookConfig


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
        hook_config: Optional hook configuration for the conversation.
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
    hook_config: HookConfig | None = Field(
        default=None,
        description=(
            "Optional hook configuration for this conversation. When set, shows what "
            "hooks are active for PreToolUse, PostToolUse, UserPromptSubmit, "
            "SessionStart, SessionEnd, and Stop events."
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

        # Show hooks if configured
        if self.hook_config:
            content.append("\n\nHooks Configured:", style="bold")
            hook_types = [
                ("PreToolUse", self.hook_config.pre_tool_use),
                ("PostToolUse", self.hook_config.post_tool_use),
                ("UserPromptSubmit", self.hook_config.user_prompt_submit),
                ("SessionStart", self.hook_config.session_start),
                ("SessionEnd", self.hook_config.session_end),
                ("Stop", self.hook_config.stop),
            ]
            for hook_type, matchers in hook_types:
                if matchers:
                    content.append(f"\n  {hook_type}: {len(matchers)} matcher(s)")
                    for matcher in matchers:
                        pattern = matcher.matcher if matcher.matcher != "*" else "(all)"
                        content.append(f"\n    [{pattern}]:")
                        for hook in matcher.hooks:
                            cmd_preview = (
                                hook.command[:50] + "..."
                                if len(hook.command) > 50
                                else hook.command
                            )
                            content.append(f"\n      - {cmd_preview}")
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
        hooks_info = ""
        if self.hook_config:
            hook_counts = []
            if self.hook_config.pre_tool_use:
                hook_counts.append(f"PreToolUse:{len(self.hook_config.pre_tool_use)}")
            if self.hook_config.post_tool_use:
                hook_counts.append(f"PostToolUse:{len(self.hook_config.post_tool_use)}")
            if self.hook_config.user_prompt_submit:
                hook_counts.append(
                    f"UserPromptSubmit:{len(self.hook_config.user_prompt_submit)}"
                )
            if self.hook_config.session_start:
                hook_counts.append(
                    f"SessionStart:{len(self.hook_config.session_start)}"
                )
            if self.hook_config.session_end:
                hook_counts.append(f"SessionEnd:{len(self.hook_config.session_end)}")
            if self.hook_config.stop:
                hook_counts.append(f"Stop:{len(self.hook_config.stop)}")
            if hook_counts:
                hooks_info = f"\n  Hooks: {', '.join(hook_counts)}"
        return (
            f"{base_str}\n  System: {prompt_preview}\n  "
            f"Tools: {tool_count} available{context_info}{hooks_info}"
        )
