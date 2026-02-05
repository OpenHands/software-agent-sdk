"""Tmux-based terminal backend implementation."""

import fcntl
import os
import time
import uuid

import libtmux
from libtmux.exc import TmuxObjectDoesNotExist

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env
from openhands.tools.terminal.constants import HISTORY_LIMIT
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal import TerminalInterface


logger = get_logger(__name__)

# Lock file for serializing tmux session creation to prevent race conditions in libtmux
# Namespaced by UID to avoid unnecessary cross-user serialization
_TMUX_LOCK_FILE = f"/tmp/openhands-tmux-session-{os.getuid()}.lock"


class TmuxTerminal(TerminalInterface):
    """Tmux-based terminal backend.

    This backend uses tmux to provide a persistent terminal session
    with full screen capture and history management capabilities.
    """

    PS1: str
    server: libtmux.Server
    session: libtmux.Session
    window: libtmux.Window
    pane: libtmux.Pane

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
    ):
        super().__init__(work_dir, username)
        self.PS1 = CmdOutputMetadata.to_ps1_prompt()

    def initialize(self) -> None:
        """Initialize the tmux terminal session."""
        if self._initialized:
            return

        env = sanitized_env()
        self.server = libtmux.Server(environment=env)
        _shell_command = "/bin/bash"
        if self.username in ["root", "openhands"]:
            # This starts a non-login (new) shell for the given user
            _shell_command = f"su {self.username} -"

        window_command = _shell_command

        logger.debug(f"Initializing tmux terminal with command: {window_command}")
        session_name = f"openhands-{self.username}-{uuid.uuid4()}"

        # Serialize tmux session creation to prevent libtmux race conditions
        # File lock provides both cross-process and intra-process synchronization
        lock_fd = None
        try:
            lock_fd = os.open(_TMUX_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o666)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            max_retries = 3
            retry_delay = 0.5
            last_error = None
            for attempt in range(max_retries):
                try:
                    self.session = self.server.new_session(
                        session_name=session_name,
                        start_directory=self.work_dir,
                        kill_session=True,
                        x=1000,
                        y=1000,
                    )
                    break
                except TmuxObjectDoesNotExist as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Tmux session creation failed (attempt {attempt + 1}/"
                            f"{max_retries}), retrying in {retry_delay}s: {e}"
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2
            else:
                raise RuntimeError(
                    f"Failed to create tmux session after {max_retries} attempts"
                ) from last_error
        finally:
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        for k, v in env.items():
            self.session.set_environment(k, v)

        # Set history limit to a large number to avoid losing history
        # https://unix.stackexchange.com/questions/43414/unlimited-history-in-tmux
        self.session.set_option("history-limit", str(HISTORY_LIMIT))
        self.session.history_limit = str(HISTORY_LIMIT)

        # Create a new pane because the initial pane's history limit is (default) 2000
        _initial_window = self.session.active_window
        self.window = self.session.new_window(
            window_name="terminal",
            window_shell=window_command,
            start_directory=self.work_dir,
        )
        active_pane = self.window.active_pane
        assert active_pane is not None, "Window should have an active pane"
        self.pane = active_pane
        logger.debug(f"pane: {self.pane}; history_limit: {self.session.history_limit}")
        _initial_window.kill()

        # Configure bash to use simple PS1 and disable PS2
        # Disable history expansion to avoid ! mangling
        self.pane.send_keys(
            f'set +H; export PROMPT_COMMAND=\'export PS1="{self.PS1}"\'; export PS2=""'
        )
        time.sleep(0.1)  # Wait for command to take effect

        logger.debug(f"Tmux terminal initialized with work dir: {self.work_dir}")
        self._initialized: bool = True
        self.clear_screen()

    def close(self) -> None:
        """Clean up the tmux session."""
        if self._closed:
            return
        try:
            if hasattr(self, "session"):
                self.session.kill()
        except Exception as e:
            # Session might already be dead/killed externally
            # (e.g., "can't find session" error from tmux)
            # Also handles ImportError during Python shutdown
            logger.debug(f"Error closing tmux session (may already be dead): {e}")
        self._closed: bool = True

    def send_keys(self, text: str, enter: bool = True) -> None:
        """Send text/keys to the tmux pane.

        Args:
            text: Text or key sequence to send
            enter: Whether to send Enter key after the text
        """
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            raise RuntimeError("Tmux terminal is not initialized")

        self.pane.send_keys(text, enter=enter)

    def read_screen(self) -> str:
        """Read the current tmux pane content.

        Returns:
            Current visible content of the tmux pane
        """
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            raise RuntimeError("Tmux terminal is not initialized")

        content = "\n".join(
            map(
                # avoid double newlines
                lambda line: line.rstrip(),
                self.pane.cmd("capture-pane", "-J", "-pS", "-").stdout,
            )
        )
        return content

    def clear_screen(self) -> None:
        """Clear the tmux pane screen and history."""
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            raise RuntimeError("Tmux terminal is not initialized")

        self.pane.send_keys("C-l", enter=False)
        time.sleep(0.1)
        self.pane.cmd("clear-history")

    def interrupt(self) -> bool:
        """Send interrupt signal (Ctrl+C) to the tmux pane.

        Returns:
            True if interrupt was sent successfully, False otherwise
        """
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            return False
        try:
            self.pane.send_keys("C-c", enter=False)
            return True
        except Exception as e:
            logger.error(f"Failed to interrupt command: {e}", exc_info=True)
            return False

    def is_running(self) -> bool:
        """Check if a command is currently running.

        For tmux, we determine this by checking if the terminal
        is ready for new commands (ends with prompt).
        """
        if not self._initialized:
            return False

        try:
            content = self.read_screen()
            # If the screen ends with our PS1 prompt, no command is running
            from openhands.tools.terminal.constants import CMD_OUTPUT_PS1_END

            return not content.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
        except Exception:
            return False
