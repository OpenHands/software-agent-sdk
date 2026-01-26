"""Execute bash tool implementation."""

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import Field


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
from rich.text import Text

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.utils import maybe_truncate
from openhands.tools.terminal.constants import (
    MAX_CMD_OUTPUT_SIZE,
    NO_CHANGE_TIMEOUT_SECONDS,
)
from openhands.tools.terminal.metadata import CmdOutputMetadata


class TerminalAction(Action):
    """Schema for bash command execution."""

    command: str = Field(
        description="The bash command to execute. Can be empty string to view additional logs when previous exit code is `-1`. Can be `C-c` (Ctrl+C) to interrupt the currently running process. Note: You can only execute one bash command at a time. If you need to run multiple commands sequentially, you can use `&&` or `;` to chain them together."  # noqa
    )
    is_input: bool = Field(
        default=False,
        description="If True, the command is an input to the running process. If False, the command is a bash command to be executed in the terminal. Default is False.",  # noqa
    )
    timeout: float | None = Field(
        default=None,
        ge=0,
        description=f"Optional. Sets a maximum time limit (in seconds) for running the command. If the command takes longer than this limit, you’ll be asked whether to continue or stop it. If you don’t set a value, the command will instead pause and ask for confirmation when it produces no new output for {NO_CHANGE_TIMEOUT_SECONDS} seconds. Use a higher value if the command is expected to take a long time (like installation or testing), or if it has a known fixed duration (like sleep).",  # noqa
    )
    reset: bool = Field(
        default=False,
        description="If True, reset the terminal by creating a new session. Use this only when the terminal becomes unresponsive. Note that all previously set environment variables and session state will be lost after reset. Cannot be used with is_input=True.",  # noqa
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation with PS1-style bash prompt."""
        content = Text()

        # Create PS1-style prompt
        content.append("$ ", style="bold green")

        # Add command with syntax highlighting
        if self.command:
            content.append(self.command, style="white")
        else:
            content.append("[empty command]", style="italic")

        # Add metadata if present
        if self.is_input:
            content.append(" ", style="white")
            content.append("(input to running process)", style="yellow")

        if self.timeout is not None:
            content.append(" ", style="white")
            content.append(f"[timeout: {self.timeout}s]", style="cyan")

        if self.reset:
            content.append(" ", style="white")
            content.append("[reset terminal]", style="red bold")

        return content


class TerminalObservation(Observation):
    """A ToolResult that can be rendered as a CLI output."""

    command: str | None = Field(
        description="The bash command that was executed. Can be empty string if the observation is from a previous command that hit soft timeout and is not yet finished.",  # noqa
    )
    exit_code: int | None = Field(
        default=None,
        description="The exit code of the command. -1 indicates the process hit the soft timeout and is not yet finished.",  # noqa
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

        # If is_error is true, prepend error message
        if self.is_error:
            llm_content.append(TextContent(text=self.ERROR_MESSAGE_HEADER))

        # TerminalObservation always has content as a single TextContent
        content_text = self.text

        ret = f"{self.metadata.prefix}{content_text}{self.metadata.suffix}"
        if self.metadata.working_dir:
            ret += f"\n[Current working directory: {self.metadata.working_dir}]"
        if self.metadata.py_interpreter_path:
            ret += f"\n[Python interpreter: {self.metadata.py_interpreter_path}]"
        if self.metadata.exit_code != -1:
            ret += f"\n[Command finished with exit code {self.metadata.exit_code}]"

        # Use enhanced truncation with file saving if working directory is available
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

        # TerminalObservation always has content as a single TextContent
        content_text = self.text

        if content_text:
            # Style the output based on content
            output_lines = content_text.split("\n")
            for line in output_lines:
                if line.strip():
                    # Color error-like lines differently
                    if any(
                        keyword in line.lower()
                        for keyword in ["error", "failed", "exception", "traceback"]
                    ):
                        text.append(line, style="red")
                    elif any(
                        keyword in line.lower() for keyword in ["warning", "warn"]
                    ):
                        text.append(line, style="yellow")
                    elif line.startswith("+ "):  # bash -x output
                        text.append(line, style="cyan")
                    else:
                        text.append(line, style="white")
                text.append("\n")

        # Add metadata with styling
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


TOOL_DESCRIPTION = """Execute a bash command in the terminal within a persistent shell session.


### Command Execution
* One command at a time: You can only execute one bash command at a time. If you need to run multiple commands sequentially, use `&&` or `;` to chain them together.
* Persistent session: Commands execute in a persistent shell session where environment variables, virtual environments, and working directory persist between commands.
* Soft timeout: Commands have a soft timeout of 10 seconds, once that's reached, you have the option to continue or interrupt the command (see section below for details)
* Shell options: Do NOT use `set -e`, `set -eu`, or `set -euo pipefail` in shell scripts or commands in this environment. The runtime may not support them and can cause unusable shell sessions. If you want to run multi-line bash commands, write the commands to a file and then run it, instead.

### Long-running Commands
* For commands that may run indefinitely, run them in the background and redirect output to a file, e.g. `python3 app.py > server.log 2>&1 &`.
* For commands that may run for a long time (e.g. installation or testing commands), or commands that run for a fixed amount of time (e.g. sleep), you should set the "timeout" parameter of your function call to an appropriate value.
* If a bash command returns exit code `-1`, this means the process hit the soft timeout and is not yet finished. By setting `is_input` to `true`, you can:
  - Send empty `command` to retrieve additional logs
  - Send text (set `command` to the text) to STDIN of the running process
  - Send control commands like `C-c` (Ctrl+C), `C-d` (Ctrl+D), or `C-z` (Ctrl+Z) to interrupt the process
  - If you do C-c, you can re-start the process with a longer "timeout" parameter to let it run to completion

### Best Practices
* Directory verification: Before creating new directories or files, first verify the parent directory exists and is the correct location.
* Directory management: Try to maintain working directory by using absolute paths and avoiding excessive use of `cd`.

### Output Handling
* Output truncation: If the output exceeds a maximum length, it will be truncated before being returned.

### Terminal Reset
* Terminal reset: If the terminal becomes unresponsive, you can set the "reset" parameter to `true` to create a new terminal session. This will terminate the current session and start fresh.
* Warning: Resetting the terminal will lose all previously set environment variables, working directory changes, and any running processes. Use this only when the terminal stops responding to commands.
"""  # noqa


class TerminalTool(ToolDefinition[TerminalAction, TerminalObservation]):
    """A ToolDefinition subclass that automatically initializes a TerminalExecutor with auto-detection."""  # noqa: E501

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        username: str | None = None,
        no_change_timeout_seconds: int | None = None,
        terminal_type: Literal["tmux", "subprocess"] | None = None,
        shell_path: str | None = None,
        executor: ToolExecutor | None = None,
    ) -> Sequence["TerminalTool"]:
        """Initialize TerminalTool with executor parameters.

        Args:
            conv_state: Conversation state to get working directory from.
                         If provided, working_dir will be taken from
                         conv_state.workspace
            username: Optional username for the bash session
            no_change_timeout_seconds: Timeout for no output change
            terminal_type: Force a specific session type:
                         ('tmux', 'subprocess').
                         If None, auto-detect based on system capabilities:
                         - On Windows: PowerShell if available, otherwise subprocess
                         - On Unix-like: tmux if available, otherwise subprocess
            shell_path: Path to the shell binary (for subprocess terminal type only).
                       If None, will auto-detect bash from PATH.
        """
        # Import here to avoid circular imports
        from openhands.tools.terminal.impl import TerminalExecutor

        working_dir = conv_state.workspace.working_dir
        if not os.path.isdir(working_dir):
            raise ValueError(f"working_dir '{working_dir}' is not a valid directory")

        # Initialize the executor
        if executor is None:
            executor = TerminalExecutor(
                working_dir=working_dir,
                username=username,
                no_change_timeout_seconds=no_change_timeout_seconds,
                terminal_type=terminal_type,
                shell_path=shell_path,
                full_output_save_dir=conv_state.env_observation_persistence_dir,
            )

        # Initialize the parent ToolDefinition with the executor
        return [
            cls(
                action_type=TerminalAction,
                observation_type=TerminalObservation,
                description=TOOL_DESCRIPTION,
                annotations=ToolAnnotations(
                    title="terminal",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# Automatically register the tool when this module is imported
register_tool(TerminalTool.name, TerminalTool)
