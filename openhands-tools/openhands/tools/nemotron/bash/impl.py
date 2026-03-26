"""Bash executor implementation (Nemotron/Anthropic-compatible).

This executor wraps TerminalExecutor to provide Anthropic-compatible bash tool.
"""

import json
from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor
from openhands.tools.nemotron.bash.definition import BashAction, BashObservation
from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.terminal_session import TerminalSession


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


logger = get_logger(__name__)


class BashExecutor(ToolExecutor[BashAction, BashObservation]):
    """Bash executor that wraps TerminalSession for Anthropic compatibility."""

    session: TerminalSession

    def __init__(
        self,
        working_dir: str,
        full_output_save_dir: str | None = None,
    ):
        """Initialize BashExecutor.

        Args:
            working_dir: Working directory for bash commands
            full_output_save_dir: Path to directory to save full output
                                  logs and files, used when truncation is needed.
        """
        self.session = create_terminal_session(
            work_dir=working_dir,
            username=None,
            no_change_timeout_seconds=None,
            terminal_type=None,  # Auto-detect
            shell_path=None,
        )
        self.session.initialize()
        self.full_output_save_dir = full_output_save_dir
        logger.info(f"BashExecutor initialized with working_dir: {working_dir}")

    def _export_envs(
        self, action: BashAction, conversation: "LocalConversation | None" = None
    ) -> None:
        if not action.command.strip():
            return

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

        _ = self.session.execute(
            TerminalAction(
                command=exports_cmd,
                is_input=False,
                timeout=None,
            )
        )

    def __call__(
        self,
        action: BashAction,
        conversation: "LocalConversation | None" = None,
    ) -> BashObservation:
        # Reset if session is closed
        if self.session._closed:
            original_work_dir = self.session.work_dir
            self.session = create_terminal_session(
                work_dir=original_work_dir,
                username=None,
                no_change_timeout_seconds=None,
                terminal_type=None,
                shell_path=None,
            )
            self.session.initialize()

        # Export environment variables if needed
        self._export_envs(action, conversation)

        # Convert BashAction to TerminalAction
        terminal_action = TerminalAction(
            command=action.command,
            is_input=False,
            timeout=None,
        )

        # Execute using terminal session
        terminal_obs = self.session.execute(terminal_action)

        # Convert TerminalObservation to BashObservation
        observation = BashObservation(
            content=terminal_obs.content,
            is_error=terminal_obs.is_error,
            command=terminal_obs.command,
            exit_code=terminal_obs.exit_code,
            timeout=terminal_obs.timeout,
            metadata=terminal_obs.metadata,
            full_output_save_dir=self.full_output_save_dir,
        )

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
                    return BashObservation.from_text(
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
