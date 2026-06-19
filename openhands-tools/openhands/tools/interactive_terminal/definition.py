"""Schema and tool definitions for exec_command / write_stdin interactive terminal."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm import TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.interactive_terminal.impl import InteractiveTerminalManager


# ──────────────────────────────────────────────────────────────────────────────
# Actions
# ──────────────────────────────────────────────────────────────────────────────


class ExecCommandAction(Action):
    """Start a shell command in a new PTY session."""

    cmd: str = Field(
        description="Shell command to execute.",
    )
    workdir: str | None = Field(
        default=None,
        description=(
            "Working directory for the command. "
            "Defaults to the conversation working directory."
        ),
    )
    yield_time_ms: int = Field(
        default=10_000,
        ge=250,
        le=30_000,
        description=(
            "Wait before yielding output. "
            "Defaults to 10000 ms; effective range is 250–30000 ms. "
            "The command continues running in the background after yielding — "
            "use write_stdin(session_id=...) to poll for more output."
        ),
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Output token budget. "
            "Defaults to no limit; larger responses may be truncated by policy."
        ),
    )

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append("$ ", style="bold green")
        text.append(self.cmd)
        if self.workdir:
            text.append(f"  [{self.workdir}]", style="dim")
        return text


class WriteStdinAction(Action):
    """Send characters to a running session or poll for output."""

    session_id: int = Field(
        description="Identifier of the running session (returned by exec_command).",
    )
    chars: str = Field(
        default="",
        description=(
            "Characters to send to stdin, delivered verbatim (byte-accurate, "
            "no Enter appended). "
            "Omit (or pass empty string) to poll without writing. "
            "Use \\x03 for Ctrl+C, \\x04 for Ctrl+D, \\x1a for Ctrl+Z. "
            "Append '\\n' explicitly when Enter is desired: chars='abc' "
            "delivers 'abc', chars='abc\\n' delivers 'abc\\n'."
        ),
    )
    yield_time_ms: int = Field(
        default=5_000,
        ge=250,
        le=30_000,
        description=(
            "Wait before yielding output. "
            "Non-empty writes default to 5000 ms; "
            "empty polls wait at least 5000 ms regardless of this value."
        ),
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Output token budget. "
            "Defaults to no limit; larger responses may be truncated by policy."
        ),
    )

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append(f"write_stdin(session_id={self.session_id}", style="bold cyan")
        if self.chars:
            text.append(f", chars={self.chars!r}", style="cyan")
        else:
            text.append(", poll", style="dim cyan")
        text.append(")", style="bold cyan")
        return text


# ──────────────────────────────────────────────────────────────────────────────
# Shared observation
# ──────────────────────────────────────────────────────────────────────────────


class InteractiveTerminalObservation(Observation):
    """Result from exec_command or write_stdin.

    Exactly one of ``session_id`` / ``exit_code`` is set:

    * ``session_id`` — process is **still running**; call
      ``write_stdin(session_id=...)`` to poll or send input.
    * ``exit_code`` — process has **completed** (0 = success).
    """

    output: str = Field(description="Command output text, possibly truncated.")
    wall_time_seconds: float = Field(
        description="Elapsed wall time spent waiting for output."
    )
    session_id: int | None = Field(
        default=None,
        description=(
            "Session identifier when the process is still running. "
            "Pass to write_stdin to poll for more output or send input."
        ),
    )
    exit_code: int | None = Field(
        default=None,
        description="Process exit code once the command has completed.",
    )
    original_token_count: int | None = Field(
        default=None,
        description="Approximate token count before any output truncation.",
    )

    @classmethod
    def create(
        cls,
        output: str,
        wall_time_seconds: float,
        session_id: int | None,
        exit_code: int | None,
        original_token_count: int | None = None,
    ) -> InteractiveTerminalObservation:
        header = _format_header(session_id, exit_code, wall_time_seconds)
        full_text = f"{header}\n{output}" if output else header
        return cls.from_text(
            text=full_text,
            output=output,
            wall_time_seconds=wall_time_seconds,
            session_id=session_id,
            exit_code=exit_code,
            original_token_count=original_token_count,
        )

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=self.text)]

    @property
    def visualize(self) -> Text:
        text = Text()
        if self.session_id is not None:
            text.append("⏳ ", style="yellow")
            text.append(
                f"Running — session_id={self.session_id}, "
                f"elapsed={self.wall_time_seconds:.2f}s",
                style="yellow",
            )
        elif self.exit_code is not None:
            ec = self.exit_code
            style = "green" if ec == 0 else "red"
            icon = "✅" if ec == 0 else "❌"
            text.append(f"{icon} ", style=style)
            text.append(
                f"Done — exit_code={ec}, elapsed={self.wall_time_seconds:.2f}s",
                style=style,
            )
        else:
            text.append("❓ ", style="dim")
            text.append("Session not found", style="dim")
        if self.output:
            text.append("\n")
            text.append(self.output[:500])
            if len(self.output) > 500:
                text.append("…", style="dim")
        return text


def _format_header(
    session_id: int | None,
    exit_code: int | None,
    wall_time_seconds: float,
) -> str:
    elapsed = f"{wall_time_seconds:.2f}s"
    if session_id is not None:
        return (
            f"[Process still running — session_id={session_id}, elapsed={elapsed}]\n"
            f"Call write_stdin(session_id={session_id}) to poll or send input."
        )
    if exit_code is not None:
        return f"[Process completed — exit_code={exit_code}, elapsed={elapsed}]"
    # session_id=None and exit_code=None: the session was not found.
    return "[Session not found — no running process with this session_id]"


# ──────────────────────────────────────────────────────────────────────────────
# Tool descriptions
# ──────────────────────────────────────────────────────────────────────────────


_EXEC_COMMAND_DESCRIPTION: Final[str] = """\
Runs a command in a PTY, returning output or a session ID for ongoing interaction.

Use ``yield_time_ms`` to control how long to wait for initial output before
returning control. The process continues running in the background after yielding.

When the response contains ``session_id``, the process is **still running** —
call ``write_stdin(session_id=...)`` to:
  * poll for more output (omit ``chars``),
  * send text or keystrokes (``chars="y\\n"``),
  * interrupt the process (``chars="\\x03"`` for Ctrl+C).

When the response contains ``exit_code``, the process has finished.

### Known limitations

* **Secret injection**: Unlike ``TerminalTool``, registered secrets are not
  automatically exported into the command environment before execution.
  Reference secrets explicitly in the command (e.g. ``cmd="echo $API_KEY"``
  will not expand ``$API_KEY`` from the secret registry).  Output masking
  (hiding secret values in observations) still applies.

### Background-monitoring pattern

```python
# Start a long-running command, return after 5 s
result = exec_command(cmd="python train.py", yield_time_ms=5000)
# result.session_id is set → process still running

# Do other work here, then check back
result = write_stdin(session_id=result.session_id)
```
"""

_WRITE_STDIN_DESCRIPTION: Final[str] = """\
Writes characters to an existing session and returns recent output.

Pass ``session_id`` from a previous ``exec_command`` call.

* **Poll** (omit ``chars`` or pass ``""``) — waits ``yield_time_ms`` then
  returns all new output.  Useful for checking on a background process.
* **Send input** (non-empty ``chars``) — writes to stdin, then waits
  ``yield_time_ms`` for a response.  ``chars`` is delivered **verbatim**:
  no Enter keystroke is appended automatically.  Common values:
    * ``"y\\n"`` — confirm a prompt (Enter included via ``\\n``)
    * ``"abc"`` — send ``abc`` with no Enter (partial stdin / raw bytes)
    * ``"\\x03"`` — Ctrl+C (interrupt)
    * ``"\\x04"`` — Ctrl+D (EOF)

  .. note::
      ``chars`` reaches the process stdin byte-for-byte.  ``chars="abc"``
      delivers ``"abc"`` and ``chars="abc\\n"`` delivers ``"abc\\n"``.
      Append ``\\n`` explicitly when Enter is desired.  This matches
      Codex's ``write_stdin`` byte contract.

The response always reports whether the session is still running
(``session_id`` present) or has finished (``exit_code`` present).
"""


# ──────────────────────────────────────────────────────────────────────────────
# Tool definitions
# ──────────────────────────────────────────────────────────────────────────────


class ExecCommandTool(
    ToolDefinition[ExecCommandAction, InteractiveTerminalObservation]
):
    """Executes a shell command and returns output or a session ID."""

    @classmethod
    def create(  # type: ignore[override]
        cls,
        conv_state: ConversationState,
        manager: InteractiveTerminalManager | None = None,
    ) -> Sequence[ExecCommandTool]:
        """Create an ExecCommandTool.

        Pass *manager* to share a process manager with a ``WriteStdinTool``; omit
        it (or use :class:`InteractiveTerminalToolSet`) to get a fresh manager.
        """
        from openhands.tools.interactive_terminal.executor import ExecCommandExecutor
        from openhands.tools.interactive_terminal.impl import get_or_create_manager

        if manager is None:
            manager = get_or_create_manager(
                str(conv_state.id), str(conv_state.workspace.working_dir)
            )
        return [
            cls(
                description=_EXEC_COMMAND_DESCRIPTION,
                action_type=ExecCommandAction,
                observation_type=InteractiveTerminalObservation,
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=ExecCommandExecutor(manager),
            )
        ]


class WriteStdinTool(ToolDefinition[WriteStdinAction, InteractiveTerminalObservation]):
    """Sends input to or polls a running terminal session."""

    @classmethod
    def create(  # type: ignore[override]
        cls,
        conv_state: ConversationState,
        manager: InteractiveTerminalManager | None = None,
    ) -> Sequence[WriteStdinTool]:
        """Create a WriteStdinTool.

        Pass *manager* to share a process manager with an ``ExecCommandTool``; omit
        it (or use :class:`InteractiveTerminalToolSet`) to get a fresh manager.
        """
        from openhands.tools.interactive_terminal.executor import WriteStdinExecutor
        from openhands.tools.interactive_terminal.impl import get_or_create_manager

        if manager is None:
            manager = get_or_create_manager(
                str(conv_state.id), str(conv_state.workspace.working_dir)
            )
        return [
            cls(
                description=_WRITE_STDIN_DESCRIPTION,
                action_type=WriteStdinAction,
                observation_type=InteractiveTerminalObservation,
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=WriteStdinExecutor(manager),
            )
        ]


class InteractiveTerminalToolSet(
    ToolDefinition[ExecCommandAction, InteractiveTerminalObservation]
):
    """Creates both exec_command and write_stdin tools backed by a shared manager.

    Usage::

        from openhands.tools.interactive_terminal import InteractiveTerminalToolSet
        from openhands.sdk import Agent, Tool

        agent = Agent(
            llm=llm,
            tools=[Tool(name=InteractiveTerminalToolSet.name)],
        )
    """

    @classmethod
    def create(
        cls,
        conv_state: ConversationState,
    ) -> list[ToolDefinition]:
        from openhands.tools.interactive_terminal.impl import get_or_create_manager

        work_dir = str(conv_state.workspace.working_dir)
        manager = get_or_create_manager(str(conv_state.id), work_dir)
        tools: list[ToolDefinition] = []
        tools.extend(ExecCommandTool.create(conv_state, manager))
        tools.extend(WriteStdinTool.create(conv_state, manager))
        return tools


register_tool(InteractiveTerminalToolSet.name, InteractiveTerminalToolSet)
register_tool(ExecCommandTool.name, ExecCommandTool)
register_tool(WriteStdinTool.name, WriteStdinTool)
