"""Unified terminal session using TerminalInterface backends."""

import re
import time
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
from openhands.tools.terminal.utils.command import (
    escape_bash_special_chars,
    split_bash_commands,
)


logger = get_logger(__name__)


class TerminalCommandStatus(Enum):
    """Status of a terminal command execution."""

    CONTINUE = "continue"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    NO_CHANGE_TIMEOUT = "no_change_timeout"
    HARD_TIMEOUT = "hard_timeout"


def _remove_command_prefix(command_output: str, command: str) -> str:
    return command_output.lstrip().removeprefix(command.lstrip()).lstrip()


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
    ) -> str:
        """Get the command output with the previous command output removed."""
        # remove the previous command output from the new output if any
        if self.prev_output:
            command_output = raw_command_output.removeprefix(self.prev_output)
            metadata.prefix = continue_prefix
        else:
            command_output = raw_command_output
        self.prev_output = raw_command_output  # update current command output anyway
        command_output = _remove_command_prefix(command_output, command)
        return command_output.rstrip()

    def _handle_completed_command(
        self,
        command: str,
        terminal_content: str,
        ps1_matches: list[re.Match],
    ) -> TerminalObservation:
        """Handle a completed command."""
        is_special_key = self._is_special_key(command)
        assert len(ps1_matches) >= 1, (
            f"Expected at least one PS1 metadata block, but got {len(ps1_matches)}.\n"
            f"---FULL OUTPUT---\n{terminal_content!r}\n---END OF OUTPUT---"
        )
        metadata = CmdOutputMetadata.from_ps1_match(ps1_matches[-1])

        # Special case where the previous command output is truncated
        # due to history limit
        get_content_before_last_match = bool(len(ps1_matches) == 1)

        # Update the current working directory if it has changed
        if metadata.working_dir != self._cwd and metadata.working_dir:
            self._cwd: str = metadata.working_dir

        logger.debug(
            f"[Prev PS1 not matched: {get_content_before_last_match}] "
            f"COMMAND OUTPUT: {terminal_content}"
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
            command,
            raw_command_output,
            metadata,
        )
        command_output = maybe_truncate(
            command_output, truncate_after=MAX_CMD_OUTPUT_SIZE
        )

        self.prev_status = TerminalCommandStatus.COMPLETED
        self.prev_output = ""  # Reset previous command output
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
    ) -> TerminalObservation:
        """Handle a command that timed out due to no output change."""
        self.prev_status = TerminalCommandStatus.NO_CHANGE_TIMEOUT
        if len(ps1_matches) != 1:
            logger.warning(
                f"Expected exactly one PS1 metadata block BEFORE the execution of a "
                f"command, but got {len(ps1_matches)} PS1 metadata blocks:\n"
                f"---\n{terminal_content!r}\n---"
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
            command,
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
    ) -> TerminalObservation:
        """Handle a command that timed out due to hard timeout."""
        self.prev_status = TerminalCommandStatus.HARD_TIMEOUT
        if len(ps1_matches) != 1:
            logger.warning(
                f"Expected exactly one PS1 metadata block BEFORE the execution of a "
                f"command, but got {len(ps1_matches)} PS1 metadata blocks:\n"
                f"---\n{terminal_content!r}\n---"
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
            command,
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
        logger.debug(f"COMBINED OUTPUT: {combined_output}")
        return combined_output

    def execute(self, action: TerminalAction) -> TerminalObservation:
        """Execute a command using the terminal backend."""
        if not self._initialized:
            raise RuntimeError("Unified session is not initialized")

        # Strip the command of any leading/trailing whitespace
        logger.debug(f"RECEIVED ACTION: {action}")
        command = action.command.strip()
        is_input: bool = action.is_input

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
        logger.debug(f"INITIAL TERMINAL OUTPUT: {initial_terminal_output!r}")

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
            logger.debug(f"PREVIOUS COMMAND OUTPUT: {raw_command_output}")
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
            logger.debug(f"RETURNING OBSERVATION (previous-command): {obs}")
            return obs

        # Send actual command/inputs to the terminal
        if command != "":
            is_special_key = self._is_special_key(command)
            if is_input:
                logger.debug(f"SENDING INPUT TO RUNNING PROCESS: {command!r}")
                self.terminal.send_keys(
                    command,
                    enter=not is_special_key,
                )
            else:
                # convert command to raw string (for bash terminals)
                if not self.terminal.is_powershell():
                    # Only escape for bash terminals, not PowerShell
                    command = escape_bash_special_chars(command)
                logger.debug(f"SENDING COMMAND: {command!r}")
                self.terminal.send_keys(
                    command,
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
                f"BEGIN OF TERMINAL CONTENT: {cur_terminal_output.split('\n')[:10]}"
            )
            logger.debug(
                f"END OF TERMINAL CONTENT: {cur_terminal_output.split('\n')[-10:]}"
            )
            ps1_matches = CmdOutputMetadata.matches_ps1_metadata(cur_terminal_output)
            current_ps1_count = len(ps1_matches)

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
                current_ps1_count > initial_ps1_count
                or cur_terminal_output.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
            ):
                obs = self._handle_completed_command(
                    command,
                    terminal_content=cur_terminal_output,
                    ps1_matches=ps1_matches,
                )
                logger.debug(f"RETURNING OBSERVATION (completed): {obs}")
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
                obs = self._handle_nochange_timeout_command(
                    command,
                    terminal_content=cur_terminal_output,
                    ps1_matches=ps1_matches,
                )
                logger.debug(f"RETURNING OBSERVATION (nochange-timeout): {obs}")
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
                    obs = self._handle_hard_timeout_command(
                        command,
                        terminal_content=cur_terminal_output,
                        ps1_matches=ps1_matches,
                        timeout=action.timeout,
                    )
                    logger.debug(f"RETURNING OBSERVATION (hard-timeout): {obs}")
                    return obs

            # Sleep before next check
            time.sleep(POLL_INTERVAL)
