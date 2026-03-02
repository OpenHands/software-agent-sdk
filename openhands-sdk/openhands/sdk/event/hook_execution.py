"""Hook execution event for observability into hook execution."""

from typing import Any, Literal

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


HookEventType = Literal[
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "Stop",
]


class HookExecutionEvent(Event):
    """Event emitted when a hook is executed.

    This event provides observability into hook execution, including:
    - Which hook type was triggered
    - The command that was run
    - The result (success/blocked/error)
    - Any output from the hook

    This allows clients to track hook execution via the event stream.
    """

    source: SourceType = Field(
        default="hook", description="Source is always 'hook' for hook execution events"
    )

    # Hook identification
    hook_event_type: HookEventType = Field(
        ..., description="The type of hook event that triggered this execution"
    )
    hook_command: str = Field(..., description="The hook command that was executed")
    tool_name: str | None = Field(
        default=None,
        description="Tool name for PreToolUse/PostToolUse hooks",
    )

    # Execution result
    success: bool = Field(..., description="Whether the hook executed successfully")
    blocked: bool = Field(
        default=False,
        description="Whether the hook blocked the operation (exit code 2 or deny)",
    )
    exit_code: int = Field(..., description="Exit code from the hook command")

    # Output
    stdout: str = Field(default="", description="Standard output from the hook")
    stderr: str = Field(default="", description="Standard error from the hook")
    reason: str | None = Field(
        default=None, description="Reason provided by hook (for blocking)"
    )
    additional_context: str | None = Field(
        default=None,
        description="Additional context injected by hook (e.g., for UserPromptSubmit)",
    )
    error: str | None = Field(
        default=None, description="Error message if hook execution failed"
    )

    # Context
    action_id: str | None = Field(
        default=None,
        description="ID of the action this hook is associated with (PreToolUse/PostToolUse)",  # noqa: E501
    )
    message_id: str | None = Field(
        default=None,
        description="ID of the message this hook is associated with (UserPromptSubmit)",
    )
    hook_input: dict[str, Any] | None = Field(
        default=None,
        description="The input data that was passed to the hook",
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this hook execution event."""
        content = Text()
        content.append("Hook: ", style="bold")
        content.append(f"{self.hook_event_type}")
        if self.tool_name:
            content.append(f" ({self.tool_name})")
        content.append("\n")

        # Status
        if self.blocked:
            content.append("Status: ", style="bold")
            content.append("BLOCKED", style="bold red")
            if self.reason:
                content.append(f" - {self.reason}")
        elif self.success:
            content.append("Status: ", style="bold")
            content.append("SUCCESS", style="bold green")
        else:
            content.append("Status: ", style="bold")
            content.append("FAILED", style="bold red")
            if self.error:
                content.append(f" - {self.error}")

        content.append(f"\nExit Code: {self.exit_code}")

        # Output (truncated)
        if self.stdout:
            output_preview = self.stdout[:200]
            if len(self.stdout) > 200:
                output_preview += "..."
            content.append(f"\nOutput: {output_preview}")

        if self.additional_context:
            content.append(f"\nInjected Context: {self.additional_context[:100]}...")

        return content

    def __str__(self) -> str:
        """Plain text string representation for HookExecutionEvent."""
        status = (
            "BLOCKED" if self.blocked else ("SUCCESS" if self.success else "FAILED")
        )
        tool_info = f" ({self.tool_name})" if self.tool_name else ""
        return f"HookExecutionEvent: {self.hook_event_type}{tool_info} - {status}"
