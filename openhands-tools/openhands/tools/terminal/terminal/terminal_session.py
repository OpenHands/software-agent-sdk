"""Unified terminal session using TerminalInterface backends."""

import re
import time
import uuid
from enum import Enum

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import maybe_truncate
from openhands.tools.terminal.constants import (
    CMD_OUTPUT_PS1_END,
    MAX_CMD_OUTPUT_SIZE,
    NO_CHANGE_TIMEOUT_SECONDS,
    POLL_INTERVAL,
    TIMEOUT_MESSAGE_TEMPLATE,
)
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    TerminalSessionBase,
)
from openhands.tools.terminal.timeout_policy import foreground_timeout_rejection_for
from openhands.tools.terminal.utils.command import (
    escape_bash_special_chars,
    group_heredoc_script_for_execution,
    split_bash_commands,
)
from openhands.tools.terminal.utils.escape_filter import TerminalQueryFilter


logger = get_logger(__name__)


class TerminalCommandStatus(Enum):
    """Status of a terminal command execution."""

    CONTINUE = "continue"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    NO_CHANGE_TIMEOUT = "no_change_timeout"
    HARD_TIMEOUT = "hard_timeout"


def _normalize_echo_line(line: str) -> str:
    """Collapse an echoed line for comparison against what was sent.

    Different backends alter the echo: readline drops leading tabs (tmux) or
    substitutes a BEL for each of them (subprocess). Ignore whitespace and
    control characters so the comparison still recognizes the echo.
    """
    return re.sub(r"[\x00-\x1f\x7f]", "", line).strip()


def _remove_command_prefix(command_output: str, command: str) -> str:
    """Strip the terminal's echo of `command` from the front of the output.

    readline consumes or rewrites leading tabs when echoing a line, so the echo
    can differ from the text that was sent by whitespace alone. An exact prefix
    match then fails and the whole wrapper (plus both halves of the completion
    marker) leaks into the observation, so fall back to comparing line by line
    with whitespace and control characters normalized.
    """
    stripped = command_output.lstrip()
    cmd = command.lstrip()
    if stripped.startswith(cmd):
        return stripped[len(cmd) :].lstrip()

    if not cmd:
        return stripped
    out_lines = stripped.split("\n")
    cmd_lines = cmd.split("\n")
    for index, cmd_line in enumerate(cmd_lines):
        if index >= len(out_lines) or _normalize_echo_line(
            out_lines[index]
        ) != _normalize_echo_line(cmd_line):
            # Not an echo of this command; leave the output untouched.
            return stripped
    return "\n".join(out_lines[len(cmd_lines) :]).lstrip()


def _remove_powershell_echo(command_output: str, command: str) -> str:
    command_output = command_output.lstrip()
    command = command.lstrip()
    first_line = command_output.splitlines()[0] if command_output else ""
    if command and command in first_line:
        _, separator, rest = command_output.partition("\n")
        command_output = rest if separator else ""
    return re.sub(r"(?:\r?\n)?PS [^\r\n]*>\s*$", "", command_output).lstrip()


class TerminalSession(TerminalSessionBase):
    """Unified bash session that works with any TerminalInterface backend.

    This class contains all the session controller logic (timeouts, command parsing,
    output processing) while delegating terminal operations to the TerminalInterface.
    """

    terminal: TerminalInterface
    prev_status: TerminalCommandStatus | None
    prev_output: str

    def __init__(
        self,
        terminal: TerminalInterface,
        no_change_timeout_seconds: int | None = None,
    ):
        """Initialize the unified session with a terminal backend.

        Args:
            terminal: The terminal backend to use
            no_change_timeout_seconds: Timeout for no output change
        """
        super().__init__(
            terminal.work_dir,
            terminal.username,
            no_change_timeout_seconds,
        )
        self.terminal = terminal
        self.no_change_timeout_seconds: int = (
            no_change_timeout_seconds or NO_CHANGE_TIMEOUT_SECONDS
        )
        # Store the last command for interactive input handling
        self.prev_status = None
        self.prev_output = ""
        # Wrapper sent to delimit a grouped heredoc script: the completion
        # marker and the exact text echoed for it. Kept on the session so that
        # a timeout, or a follow-up poll with an empty command, can strip both
        # from the screen the same way a completed command does.
        self._boundary_marker: str | None = None
        self._wrapper_echo: str | None = None
        # Stateful filter for terminal query sequences (handles split sequences)
        self._query_filter = TerminalQueryFilter()

    @classmethod
    def attach_to_existing(
        cls,
        terminal: TerminalInterface,
        no_change_timeout_seconds: int | None = None,
    ) -> "TerminalSession":
        """Create a TerminalSession for an already-initialized terminal.

        Use this instead of ``__init__`` + ``initialize()`` when the
        terminal has already been set up (e.g. by a pane pool) and
        calling ``initialize()`` again would create a duplicate session.
        """
        session = cls(terminal, no_change_timeout_seconds)
        session._initialized = True
        return session

    def initialize(self) -> None:
        """Initialize the terminal backend."""
        self.terminal.initialize()
        self._initialized: bool = True
        logger.debug(f"Unified session initialized with {type(self.terminal).__name__}")

    def close(self) -> None:
        """Clean up the terminal backend."""
        if self._closed:
            return
        self.terminal.close()
        self._closed: bool = True

    def interrupt(self) -> bool:
        """Interrupt the currently running command (equivalent to Ctrl+C)."""
        return self.terminal.interrupt()

    def is_running(self) -> bool:
        """Check if a command is currently running."""
        if not self._initialized:
            return False
        return self.prev_status in {
            TerminalCommandStatus.CONTINUE,
            TerminalCommandStatus.NO_CHANGE_TIMEOUT,
            TerminalCommandStatus.HARD_TIMEOUT,
        }

    def _is_special_key(self, command: str) -> bool:
        """Check if the command is a special key."""
        # Special keys are of the form C-<key>
        _command = command.strip()
        return _command.startswith("C-") and len(_command) == 3

    def _get_command_output(
        self,
        command: str,
        raw_command_output: str,
        metadata: CmdOutputMetadata,
        continue_prefix: str = "",
        is_final: bool = False,
    ) -> str:
        """Get the command output with the previous command output removed.

        Also filters terminal query sequences that could cause visible escape
        code garbage when the output is displayed. Uses stateful filtering to
        handle escape sequences that may be split across incremental outputs.
        See: https://github.com/OpenHands/software-agent-sdk/issues/2244

        Args:
            command: The command being executed
            raw_command_output: Raw output from terminal
            metadata: Output metadata to populate
            continue_prefix: Prefix for continuation output
            is_final: If True, flush any pending filter state (command completed)
        """
        # remove the previous command output from the new output if any
        if self.prev_output:
            command_output = raw_command_output.removeprefix(self.prev_output)
            metadata.prefix = continue_prefix
        else:
            command_output = raw_command_output
        self.prev_output = raw_command_output  # update current command output anyway
        if self.terminal.is_powershell():
            command_output = _remove_powershell_echo(command_output, command)
        else:
            command_output = _remove_command_prefix(command_output, command)

        # Filter terminal query sequences that would cause the terminal to
        # respond when displayed, producing visible garbage.
        # The filter is stateful to handle sequences split across chunks.
        command_output = self._query_filter.filter(command_output)
        if is_final:
            # Flush any pending bytes when command completes
            command_output += self._query_filter.flush()

        return command_output.rstrip()

    def _handle_completed_command(
        self,
        command: str,
        terminal_content: str,
        ps1_matches: list[re.Match],
        echoed_command: str | None = None,
    ) -> TerminalObservation:
        """Handle a completed command."""
        is_special_key = self._is_special_key(command)
        output_command = echoed_command or command

        # When PS1 metadata markers are missing (e.g., corrupted by TUI/ANSI
        # output or scrolled off-screen), fall back gracefully instead of
        # crashing. The command likely completed but we can't extract the
        # exit code or working directory.
        if len(ps1_matches) == 0:
            logger.warning(
                "No PS1 metadata found in terminal output. "
                "Command output may have overwritten the markers "
                "(e.g., TUI rendering, large output)."
            )
            metadata = CmdOutputMetadata(exit_code=-1, working_dir=self._cwd)
            metadata.suffix = (
                "\n[The command completed but the exit code could not "
                "be determined. Terminal output may have corrupted the "
                "PS1 metadata markers.]"
            )
            command_output = self._get_command_output(
                output_command,
                terminal_content,
                metadata,
                is_final=True,
            )
            command_output = maybe_truncate(
                command_output, truncate_after=MAX_CMD_OUTPUT_SIZE
            )
            self.prev_status = TerminalCommandStatus.COMPLETED
            self.prev_output = ""
            self._query_filter.reset()
            self._ready_for_next_command()
            return TerminalObservation.from_text(
                command=command,
                text=command_output,
                metadata=metadata,
                exit_code=metadata.exit_code,
            )

        metadata = CmdOutputMetadata.from_ps1_match(ps1_matches[-1])

        # Special case where the previous command output is truncated
        # due to history limit
        get_content_before_last_match = bool(len(ps1_matches) == 1)

        # Update the current working directory if it has changed
        if metadata.working_dir != self._cwd and metadata.working_dir:
            self._cwd: str = metadata.working_dir

        logger.debug(
            "Parsed terminal output (previous_ps1_not_matched=%s, content_length=%s)",
            get_content_before_last_match,
            len(terminal_content),
        )
        # Extract the command output between the two PS1 prompts
        raw_command_output = self._combine_outputs_between_matches(
            terminal_content,
            ps1_matches,
            get_content_before_last_match=get_content_before_last_match,
        )

        if get_content_before_last_match:
            # Count the number of lines in the truncated output
            num_lines = len(raw_command_output.splitlines())
            metadata.prefix = (
                f"[Previous command outputs are truncated. "
                f"Showing the last {num_lines} lines of the output below.]\n"
            )

        metadata.suffix = (
            f"\n[The command completed with exit code {metadata.exit_code}.]"
            if not is_special_key
            else (
                f"\n[The command completed with exit code {metadata.exit_code}. "
                f"CTRL+{command[-1].upper()} was sent.]"
            )
        )
        command_output = self._get_command_output(
            output_command,
            raw_command_output,
            metadata,
            is_final=True,  # Command completed, flush filter state
        )
        command_output = maybe_truncate(
            command_output, truncate_after=MAX_CMD_OUTPUT_SIZE
        )

        self.prev_status = TerminalCommandStatus.COMPLETED
        self.prev_output = ""  # Reset previous command output
        self._query_filter.reset()  # Reset filter for next command
        self._ready_for_next_command()
        return TerminalObservation.from_text(
            command=command,
            text=command_output,
            metadata=metadata,
            exit_code=metadata.exit_code,
        )

    def _handle_nochange_timeout_command(
        self,
        command: str,
        terminal_content: str,
        ps1_matches: list[re.Match],
        echoed_command: str | None = None,
    ) -> TerminalObservation:
        """Handle a command that timed out due to no output change."""
        self.prev_status = TerminalCommandStatus.NO_CHANGE_TIMEOUT
        if len(ps1_matches) != 1:
            logger.warning(
                "Expected exactly one PS1 metadata block before command execution, "
                "but got %s (content_length=%s)",
                len(ps1_matches),
                len(terminal_content),
            )
        raw_command_output = self._combine_outputs_between_matches(
            terminal_content, ps1_matches
        )
        metadata = CmdOutputMetadata()  # No metadata available
        metadata.suffix = (
            f"\n[The command has no new output after "
            f"{self.no_change_timeout_seconds} seconds. {TIMEOUT_MESSAGE_TEMPLATE}]"
        )
        command_output = self._get_command_output(
            echoed_command or command,
            raw_command_output,
            metadata,
            continue_prefix="[Below is the output of the previous command.]\n",
        )
        command_output = maybe_truncate(
            command_output, truncate_after=MAX_CMD_OUTPUT_SIZE
        )
        return TerminalObservation.from_text(
            command=command,
            text=command_output,
            metadata=metadata,
            exit_code=metadata.exit_code,
        )

    def _handle_hard_timeout_command(
        self,
        command: str,
        terminal_content: str,
        ps1_matches: list[re.Match],
        timeout: float,
        echoed_command: str | None = None,
    ) -> TerminalObservation:
        """Handle a command that timed out due to hard timeout."""
        self.prev_status = TerminalCommandStatus.HARD_TIMEOUT
        if len(ps1_matches) != 1:
            logger.warning(
                "Expected exactly one PS1 metadata block before command execution, "
                "but got %s (content_length=%s)",
                len(ps1_matches),
                len(terminal_content),
            )
        raw_command_output = self._combine_outputs_between_matches(
            terminal_content, ps1_matches
        )
        metadata = CmdOutputMetadata()  # No metadata available
        metadata.suffix = (
            f"\n[The command timed out after {timeout} seconds. "
            f"{TIMEOUT_MESSAGE_TEMPLATE}]"
        )
        command_output = self._get_command_output(
            echoed_command or command,
            raw_command_output,
            metadata,
            continue_prefix="[Below is the output of the previous command.]\n",
        )
        command_output = maybe_truncate(
            command_output, truncate_after=MAX_CMD_OUTPUT_SIZE
        )
        return TerminalObservation.from_text(
            command=command,
            exit_code=metadata.exit_code,
            text=command_output,
            metadata=metadata,
        )

    def _ready_for_next_command(self) -> None:
        """Reset the content buffer for a new command."""
        # Clear the current content
        self.terminal.clear_screen()

    def _strip_boundary_wrapper(self, terminal_content: str) -> str:
        """Remove the grouped-heredoc wrapper from a raw screen read.

        A completed command strips the completion marker before parsing, but
        the timeout paths and a follow-up poll (empty command) read the screen
        directly. Without this, the wrapper echo and both halves of the marker
        leak into the observation the agent sees.
        """
        if self._boundary_marker is not None:
            terminal_content = terminal_content.replace(self._boundary_marker, "")
            terminal_content = terminal_content.replace("\x1b[?2004l", "").replace(
                "\x1b[?2004h", ""
            )
        if self._wrapper_echo:
            terminal_content = _remove_command_prefix(
                terminal_content, self._wrapper_echo
            )
        return terminal_content

    def _combine_outputs_between_matches(
        self,
        terminal_content: str,
        ps1_matches: list[re.Match],
        get_content_before_last_match: bool = False,
    ) -> str:
        """Combine all outputs between PS1 matches."""
        if len(ps1_matches) == 1:
            if get_content_before_last_match:
                # The command output is the content before the last PS1 prompt
                return terminal_content[: ps1_matches[0].start()]
            else:
                # The command output is the content after the last PS1 prompt
                return terminal_content[ps1_matches[0].end() + 1 :]
        elif len(ps1_matches) == 0:
            return terminal_content
        combined_output = ""
        for i in range(len(ps1_matches) - 1):
            # Extract content between current and next PS1 prompt
            output_segment = terminal_content[
                ps1_matches[i].end() + 1 : ps1_matches[i + 1].start()
            ]
            combined_output += output_segment + "\n"
        # Add the content after the last PS1 prompt
        combined_output += terminal_content[ps1_matches[-1].end() + 1 :]
        logger.debug(
            "Combined terminal output (content_length=%s)", len(combined_output)
        )
        return combined_output

    def execute(self, action: TerminalAction) -> TerminalObservation:
        """Execute a command using the terminal backend."""
        if not self._initialized:
            raise RuntimeError("Unified session is not initialized")

        # Strip the command of any leading/trailing whitespace
        logger.debug(
            "Received terminal action (is_input=%s, command_length=%s)",
            action.is_input,
            len(action.command),
        )
        command = action.command.strip()
        is_input: bool = action.is_input

        rejection = foreground_timeout_rejection_for(
            command=command,
            is_input=is_input,
            timeout=action.timeout,
        )
        if rejection is not None:
            return TerminalObservation.from_text(
                text=rejection,
                command=command,
                is_error=True,
            )

        # If the previous command is not completed,
        # we need to check if the command is empty
        if self.prev_status not in {
            TerminalCommandStatus.CONTINUE,
            TerminalCommandStatus.NO_CHANGE_TIMEOUT,
            TerminalCommandStatus.HARD_TIMEOUT,
        }:
            if command == "":
                return TerminalObservation.from_text(
                    text="No previous running command to retrieve logs from.",
                    command=command,
                    is_error=True,
                )
            if is_input:
                return TerminalObservation.from_text(
                    text="No previous running command to interact with.",
                    command=command,
                    is_error=True,
                )

        # Check if the command is a single command or multiple commands
        splited_commands = split_bash_commands(command)
        if len(splited_commands) > 1:
            commands_list = "\n".join(
                f"({i + 1}) {cmd}" for i, cmd in enumerate(splited_commands)
            )
            return TerminalObservation.from_text(
                text=(
                    "Cannot execute multiple commands at once.\n"
                    "Please run each command separately OR chain them into a single "
                    f"command via && or ;\nProvided commands:\n{commands_list}"
                ),
                command=command,
                is_error=True,
            )

        # Get initial state before sending command
        initial_terminal_output = self.terminal.read_screen()
        initial_ps1_matches = CmdOutputMetadata.matches_ps1_metadata(
            initial_terminal_output
        )
        initial_ps1_count = len(initial_ps1_matches)
        logger.debug(f"Initial PS1 count: {initial_ps1_count}")
        logger.debug(
            "Initial terminal output (content_length=%s)",
            len(initial_terminal_output),
        )

        start_time = time.time()
        last_change_time = start_time
        last_terminal_output = initial_terminal_output

        # When prev command is still running, and we are trying to send a new command
        if (
            self.prev_status
            in {
                TerminalCommandStatus.HARD_TIMEOUT,
                TerminalCommandStatus.NO_CHANGE_TIMEOUT,
            }
            and not last_terminal_output.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
            and not is_input
            and command != ""
        ):
            last_terminal_output = self._strip_boundary_wrapper(last_terminal_output)
            _ps1_matches = CmdOutputMetadata.matches_ps1_metadata(last_terminal_output)
            # Use initial_ps1_matches if _ps1_matches is empty,
            # otherwise use _ps1_matches. This handles the case where
            # the prompt might be scrolled off screen but existed before
            current_matches_for_output = (
                _ps1_matches if _ps1_matches else initial_ps1_matches
            )
            raw_command_output = self._combine_outputs_between_matches(
                last_terminal_output, current_matches_for_output
            )
            metadata = CmdOutputMetadata()  # No metadata available
            metadata.suffix = (
                f'\n[Your command "{command}" is NOT executed. The previous command '
                f"is still running - You CANNOT send new commands until the previous "
                f"command is completed. By setting `is_input` to `true`, you can "
                f"interact with the current process: {TIMEOUT_MESSAGE_TEMPLATE}]"
            )
            logger.debug(
                "Previous command output (content_length=%s)",
                len(raw_command_output),
            )
            command_output = self._get_command_output(
                command,
                raw_command_output,
                metadata,
                continue_prefix="[Below is the output of the previous command.]\n",
            )
            command_output = maybe_truncate(
                command_output, truncate_after=MAX_CMD_OUTPUT_SIZE
            )
            obs = TerminalObservation.from_text(
                command=command,
                text=command_output,
                metadata=metadata,
                exit_code=metadata.exit_code,
                is_error=True,
            )
            return obs

        command_to_send = command
        command_boundary_marker: str | None = None
        if command and not is_input:
            self._boundary_marker = None
            self._wrapper_echo = None
        if command and not is_input and not self.terminal.is_powershell():
            command_to_send = group_heredoc_script_for_execution(command_to_send)
            if command_to_send != command:
                command_boundary_marker = f"__OH_COMMAND_FINISHED_{uuid.uuid4().hex}__"
                marker_midpoint = len(command_boundary_marker) // 2
                marker_start = command_boundary_marker[:marker_midpoint]
                marker_end = command_boundary_marker[marker_midpoint:]
                command_to_send += (
                    "; (__openhands_status=$?; printf '\\n%s%s\\n' "
                    f"'{marker_start}' '{marker_end}'; "
                    'exit "$__openhands_status")'
                )
                # Remember what we wrapped so a timeout or follow-up poll can
                # strip the echo even though it never sees this local value.
                self._boundary_marker = command_boundary_marker
            command_to_send = escape_bash_special_chars(command_to_send)
            if command_boundary_marker is not None:
                # The terminal echoes the escaped text, so match against that.
                self._wrapper_echo = command_to_send

        # Send actual command/inputs to the terminal
        sent_command = command != ""
        if command != "":
            is_special_key = self._is_special_key(command)
            if is_input:
                logger.debug(
                    "Sending input to running process (input_length=%s)", len(command)
                )
                self.terminal.send_keys(
                    command,
                    enter=not is_special_key,
                )
            else:
                logger.debug(
                    "Sending command (command_length=%s)", len(command_to_send)
                )
                self.terminal.send_keys(
                    command_to_send,
                    enter=not is_special_key,
                )

        # Loop until the command completes or times out
        while True:
            _start_time = time.time()
            logger.debug(f"GETTING TERMINAL CONTENT at {_start_time}")
            cur_terminal_output = self.terminal.read_screen()
            logger.debug(
                f"TERMINAL CONTENT GOT after {time.time() - _start_time:.2f} seconds"
            )
            logger.debug(
                "Terminal content read (content_length=%s)", len(cur_terminal_output)
            )
            ps1_matches = CmdOutputMetadata.matches_ps1_metadata(cur_terminal_output)
            current_ps1_count = len(ps1_matches)
            output_changed_since_command = (
                cur_terminal_output != initial_terminal_output
            )
            command_reached_boundary = (
                command_boundary_marker is None
                or command_boundary_marker in cur_terminal_output
            )

            if cur_terminal_output != last_terminal_output:
                last_terminal_output = cur_terminal_output
                last_change_time = time.time()
                logger.debug(f"CONTENT UPDATED DETECTED at {last_change_time}")

            # 1) Execution completed:
            # Condition 1: A new prompt has appeared since the command started.
            # Condition 2: The prompt count hasn't increased (potentially because the
            # initial one scrolled off), BUT the *current* visible terminal ends with a
            # prompt, indicating completion.
            if (
                command_reached_boundary
                and (not sent_command or output_changed_since_command)
                and (
                    current_ps1_count > initial_ps1_count
                    or cur_terminal_output.rstrip().endswith(
                        CMD_OUTPUT_PS1_END.rstrip()
                    )
                )
            ):
                completed_terminal_output = cur_terminal_output
                completed_ps1_matches = ps1_matches
                # A follow-up poll sends no command, so fall back to the wrapper
                # tracked on the session to strip it from the screen.
                effective_marker = command_boundary_marker or self._boundary_marker
                effective_echo = (
                    command_to_send
                    if command_boundary_marker is not None
                    else self._wrapper_echo
                )
                if effective_marker is not None:
                    completed_terminal_output = completed_terminal_output.replace(
                        effective_marker, ""
                    )
                    completed_terminal_output = completed_terminal_output.replace(
                        "\x1b[?2004l", ""
                    ).replace("\x1b[?2004h", "")
                    completed_ps1_matches = CmdOutputMetadata.matches_ps1_metadata(
                        completed_terminal_output
                    )
                obs = self._handle_completed_command(
                    command,
                    terminal_content=completed_terminal_output,
                    ps1_matches=completed_ps1_matches,
                    echoed_command=effective_echo,
                )
                return obs

            # Timeout checks should only trigger if a new prompt hasn't appeared yet.

            # 2) Execution timed out since there's no change in output
            # for a while (NO_CHANGE_TIMEOUT_SECONDS)
            # We ignore this if the command is *blocking*
            time_since_last_change = time.time() - last_change_time
            is_blocking = action.timeout is not None
            logger.debug(
                f"CHECKING NO CHANGE TIMEOUT ({self.no_change_timeout_seconds}s): "
                f"elapsed {time_since_last_change}. Action blocking: {is_blocking}"
            )
            if (
                not is_blocking
                and self.no_change_timeout_seconds is not None
                and time_since_last_change >= self.no_change_timeout_seconds
            ):
                stripped_output = self._strip_boundary_wrapper(cur_terminal_output)
                obs = self._handle_nochange_timeout_command(
                    command,
                    terminal_content=stripped_output,
                    ps1_matches=CmdOutputMetadata.matches_ps1_metadata(stripped_output),
                    echoed_command=(
                        command_to_send if command_boundary_marker is not None else None
                    ),
                )
                return obs

            # 3) Execution timed out since the command has been running for too long
            # (hard timeout)
            elapsed_time = time.time() - start_time
            logger.debug(
                f"CHECKING HARD TIMEOUT ({action.timeout}s): elapsed {elapsed_time:.2f}"
            )
            if action.timeout is not None:
                time_since_start = time.time() - start_time
                if time_since_start >= action.timeout:
                    stripped_output = self._strip_boundary_wrapper(cur_terminal_output)
                    obs = self._handle_hard_timeout_command(
                        command,
                        terminal_content=stripped_output,
                        ps1_matches=CmdOutputMetadata.matches_ps1_metadata(
                            stripped_output
                        ),
                        timeout=action.timeout,
                        echoed_command=(
                            command_to_send
                            if command_boundary_marker is not None
                            else None
                        ),
                    )
                    return obs

            # Sleep before next check
            time.sleep(POLL_INTERVAL)
