import json
from typing import TYPE_CHECKING, Literal

from openhands.sdk.llm import TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)
from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.terminal_session import TerminalSession


logger = get_logger(__name__)


class TerminalExecutor(ToolExecutor[TerminalAction, TerminalObservation]):
    session: TerminalSession
    shell_path: str | None

    def __init__(
        self,
        working_dir: str,
        username: str | None = None,
        no_change_timeout_seconds: int | None = None,
        terminal_type: Literal["tmux", "subprocess"] | None = None,
        shell_path: str | None = None,
        full_output_save_dir: str | None = None,
    ):
        """Initialize TerminalExecutor with auto-detected or specified session type.

        Args:
            working_dir: Working directory for bash commands
            username: Optional username for the bash session
            no_change_timeout_seconds: Timeout for no output change
            terminal_type: Force a specific session type:
                         ('tmux', 'subprocess').
                         If None, auto-detect based on system capabilities
            shell_path: Path to the shell binary (for subprocess terminal type only).
                       If None, will auto-detect bash from PATH.
            full_output_save_dir: Path to directory to save full output
                                  logs and files, used when truncation is needed.
        """
        self.shell_path = shell_path
        self.session = create_terminal_session(
            work_dir=working_dir,
            username=username,
            no_change_timeout_seconds=no_change_timeout_seconds,
            terminal_type=terminal_type,
            shell_path=shell_path,
        )
        self.session.initialize()
        self.full_output_save_dir: str | None = full_output_save_dir
        logger.info(
            f"TerminalExecutor initialized with working_dir: {working_dir}, "
            f"username: {username}, "
            f"terminal_type: {terminal_type or self.session.__class__.__name__}"
        )

    def _export_envs(
        self, action: TerminalAction, conversation: "LocalConversation | None" = None
    ) -> None:
        if not action.command.strip():
            return

        if action.is_input:
            return

        # Get secrets from conversation
        env_vars = {}
        if conversation is not None:
            try:
                secret_registry = conversation.state.secret_registry
                env_vars = secret_registry.get_secrets_as_env_vars(action.command)
            except Exception:
                env_vars = {}

        if not env_vars:
            return

        export_statements = []
        for key, value in env_vars.items():
            export_statements.append(f"export {key}={json.dumps(value)}")
        exports_cmd = " && ".join(export_statements)

        logger.debug(f"Exporting {len(env_vars)} environment variables before command")

        # Execute the export command separately to persist env in the session
        _ = self.session.execute(
            TerminalAction(
                command=exports_cmd,
                is_input=False,
                timeout=action.timeout,
            )
        )

    def reset(self) -> TerminalObservation:
        """Reset the terminal session by creating a new instance.

        Returns:
            TerminalObservation with reset confirmation message
        """
        original_work_dir = self.session.work_dir
        original_username = self.session.username
        original_no_change_timeout = self.session.no_change_timeout_seconds

        self.session.close()
        self.session = create_terminal_session(
            work_dir=original_work_dir,
            username=original_username,
            no_change_timeout_seconds=original_no_change_timeout,
            terminal_type=None,  # Let it auto-detect like before
            shell_path=self.shell_path,
        )
        self.session.initialize()

        logger.info(
            f"Terminal session reset successfully with working_dir: {original_work_dir}"
        )

        return TerminalObservation.from_text(
            text=(
                "Terminal session has been reset. All previous environment "
                "variables and session state have been cleared."
            ),
            command="[RESET]",
            exit_code=0,
        )

    def __call__(
        self,
        action: TerminalAction,
        conversation: "LocalConversation | None" = None,
    ) -> TerminalObservation:
        # Validate field combinations
        if action.reset and action.is_input:
            raise ValueError("Cannot use reset=True with is_input=True")

        if action.reset or self.session._closed:
            reset_result = self.reset()

            # Handle command execution after reset
            if action.command.strip():
                command_action = TerminalAction(
                    command=action.command,
                    timeout=action.timeout,
                    is_input=False,  # is_input validated to be False when reset=True
                )
                self._export_envs(command_action, conversation)
                command_result = self.session.execute(command_action)

                # Extract text from content
                reset_text = reset_result.text
                command_text = command_result.text

                observation = command_result.model_copy(
                    update={
                        "content": [
                            TextContent(text=f"{reset_text}\n\n{command_text}")
                        ],
                        "command": f"[RESET] {action.command}",
                    }
                )
            else:
                # Reset only, no command to execute
                observation = reset_result
        else:
            # If env keys detected, export env values to bash as a separate action first
            self._export_envs(action, conversation)
            observation = self.session.execute(action)

        # Apply automatic secrets masking
        content_text = observation.text

        if content_text and conversation is not None:
            try:
                secret_registry = conversation.state.secret_registry
                masked_content = secret_registry.mask_secrets_in_output(content_text)
                if masked_content:
                    data = observation.model_dump(
                        exclude={"content", "full_output_save_dir"}
                    )
                    return TerminalObservation.from_text(
                        text=masked_content,
                        full_output_save_dir=self.full_output_save_dir,
                        **data,
                    )
            except Exception:
                pass

        return observation

    def close(self) -> None:
        """Close the terminal session and clean up resources."""
        if hasattr(self, "session"):
            self.session.close()
