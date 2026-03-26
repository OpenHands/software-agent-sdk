"""Bash tool definition (Nemotron/Anthropic-compatible).

This is a thin wrapper around TerminalExecutor that exposes the tool as "bash"
instead of "terminal", matching Anthropic's bash tool schema exactly.
"""

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)
from openhands.tools.terminal.constants import (
    MAX_CMD_OUTPUT_SIZE,
    NO_CHANGE_TIMEOUT_SECONDS,
)
from openhands.tools.terminal.metadata import CmdOutputMetadata


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.utils import maybe_truncate


class BashAction(Action):
    """Schema for bash command execution (Anthropic-compatible).

    This matches the Anthropic bash tool schema exactly:
    - command: str (required)
    """

    command: str = Field(
        description=(
            "The bash command to execute. Can be empty string to view additional "
            "logs when previous exit code is `-1`. Can be `C-c` (Ctrl+C) to "
            "interrupt the currently running process. Note: You can only execute "
            "one bash command at a time. If you need to run multiple commands "
            "sequentially, you can use `&&` or `;` to chain them together."
        )
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation with PS1-style bash prompt."""
        content = Text()
        content.append("$ ", style="bold green")
        if self.command:
            content.append(self.command, style="white")
        else:
            content.append("[empty command]", style="italic")
        return content


class BashObservation(Observation):
    """A ToolResult that can be rendered as a CLI output."""

    command: str | None = Field(
        description=(
            "The bash command that was executed. Can be empty string if the "
            "observation is from a previous command that hit soft timeout and "
            "is not yet finished."
        ),
    )
    exit_code: int | None = Field(
        default=None,
        description=(
            "The exit code of the command. -1 indicates the process hit the "
            "soft timeout and is not yet finished."
        ),
    )
    timeout: bool = Field(
        default=False, description="Whether the command execution timed out."
    )
    metadata: CmdOutputMetadata = Field(
        default_factory=CmdOutputMetadata,
        description="Additional metadata captured from PS1 after command execution.",
    )
    full_output_save_dir: str | None = Field(
        default=None,
        description="Directory where full output files are saved",
    )

    @property
    def command_id(self) -> int | None:
        """Get the command ID from metadata."""
        return self.metadata.pid

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        llm_content: list[TextContent | ImageContent] = []

        if self.is_error:
            llm_content.append(TextContent(text=self.ERROR_MESSAGE_HEADER))

        content_text = self.text

        ret = f"{self.metadata.prefix}{content_text}{self.metadata.suffix}"
        if self.metadata.working_dir:
            ret += f"\n[Current working directory: {self.metadata.working_dir}]"
        if self.metadata.py_interpreter_path:
            ret += f"\n[Python interpreter: {self.metadata.py_interpreter_path}]"
        if self.metadata.exit_code != -1:
            ret += f"\n[Command finished with exit code {self.metadata.exit_code}]"

        truncated_text = maybe_truncate(
            content=ret,
            truncate_after=MAX_CMD_OUTPUT_SIZE,
            save_dir=self.full_output_save_dir,
            tool_prefix="bash",
        )
        llm_content.append(TextContent(text=truncated_text))

        return llm_content

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation with terminal-style output formatting."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")

        content_text = self.text

        if content_text:
            output_lines = content_text.split("\n")
            for line in output_lines:
                if line.strip():
                    if any(
                        keyword in line.lower()
                        for keyword in ["error", "failed", "exception", "traceback"]
                    ):
                        text.append(line, style="red")
                    elif any(
                        keyword in line.lower() for keyword in ["warning", "warn"]
                    ):
                        text.append(line, style="yellow")
                    elif line.startswith("+ "):
                        text.append(line, style="cyan")
                    else:
                        text.append(line, style="white")
                text.append("\n")

        if hasattr(self, "metadata") and self.metadata:
            if self.metadata.working_dir:
                text.append("\n📁 ", style="blue")
                text.append(
                    f"Working directory: {self.metadata.working_dir}", style="blue"
                )

            if self.metadata.py_interpreter_path:
                text.append("\n🐍 ", style="green")
                text.append(
                    f"Python interpreter: {self.metadata.py_interpreter_path}",
                    style="green",
                )

            if (
                hasattr(self.metadata, "exit_code")
                and self.metadata.exit_code is not None
            ):
                if self.metadata.exit_code == 0:
                    text.append("\n✅ ", style="green")
                    text.append(f"Exit code: {self.metadata.exit_code}", style="green")
                elif self.metadata.exit_code == -1:
                    text.append("\n⏳ ", style="yellow")
                    text.append("Process still running (soft timeout)", style="yellow")
                else:
                    text.append("\n❌ ", style="red")
                    text.append(f"Exit code: {self.metadata.exit_code}", style="red")

        return text


TOOL_DESCRIPTION = f"""Run a shell command and return stdout/stderr.

### Command Execution
* One command at a time: You can only execute one bash command at a time. \
If you need to run multiple commands sequentially, use `&&` or `;` to chain \
them together.
* Persistent session: Commands execute in a persistent shell session where \
environment variables, virtual environments, and working directory persist \
between commands.
* Soft timeout: Commands have a soft timeout of {NO_CHANGE_TIMEOUT_SECONDS} \
seconds, once that's reached, you have the option to continue or interrupt the \
command.

### Long-running Commands
* For commands that may run indefinitely, run them in the background and \
redirect output to a file, e.g. `python3 app.py > server.log 2>&1 &`.
* If a bash command returns exit code `-1`, this means the process hit the \
soft timeout and is not yet finished. Send empty `command` to retrieve \
additional logs or send `C-c` to interrupt.

### Output Handling
* Output truncation: If the output exceeds a maximum length, it will be \
truncated before being returned.
"""


class BashTool(ToolDefinition[BashAction, BashObservation]):
    """Bash tool (Anthropic-compatible) that wraps TerminalExecutor."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["BashTool"]:
        """Initialize BashTool with executor parameters.

        Args:
            conv_state: Conversation state to get working directory from.
        """
        from openhands.tools.nemotron.bash.impl import BashExecutor

        working_dir = conv_state.workspace.working_dir
        if not os.path.isdir(working_dir):
            raise ValueError(f"working_dir '{working_dir}' is not a valid directory")

        executor = BashExecutor(
            working_dir=working_dir,
            full_output_save_dir=conv_state.env_observation_persistence_dir,
        )

        return [
            cls(
                action_type=BashAction,
                observation_type=BashObservation,
                description=TOOL_DESCRIPTION,
                annotations=ToolAnnotations(
                    title="bash",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


register_tool(BashTool.name, BashTool)
