"""Tmux-based terminal backend implementation."""

import logging
import time
import uuid
from collections.abc import Mapping

import libtmux

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.redact import redact_api_key_literals
from openhands.tools.terminal.constants import (
    CMD_OUTPUT_PS1_END,
    HISTORY_LIMIT,
    TMUX_SESSION_HEIGHT,
    TMUX_SESSION_WIDTH,
    TMUX_SOCKET_NAME,
)
from openhands.tools.terminal.env import (
    build_terminal_env,
    normalize_terminal_env,
)
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal import TerminalInterface
from openhands.tools.terminal.terminal.interface import parse_ctrl_key


logger = get_logger(__name__)


class _SecretRedactFilter(logging.Filter):
    """Redact API key literals from libtmux log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            record.msg = redact_api_key_literals(record.msg)
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = {
                    k: redact_api_key_literals(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_api_key_literals(a) if isinstance(a, str) else a
                    for a in record.args
                )
        tmux_cmd = getattr(record, "tmux_cmd", None)
        if tmux_cmd and isinstance(tmux_cmd, str):
            record.tmux_cmd = redact_api_key_literals(tmux_cmd)
        return True


def _install_libtmux_redaction_filter() -> None:
    logger_names = [
        "libtmux",
        *(
            name
            for name, candidate in logging.Logger.manager.loggerDict.items()
            if name.startswith("libtmux.") and isinstance(candidate, logging.Logger)
        ),
    ]
    for logger_name in logger_names:
        libtmux_logger = logging.getLogger(logger_name)
        if not any(
            isinstance(log_filter, _SecretRedactFilter)
            for log_filter in libtmux_logger.filters
        ):
            libtmux_logger.addFilter(_SecretRedactFilter())


# Map normalized special key names to tmux key names.
_TMUX_SPECIALS: dict[str, str] = {
    "ENTER": "Enter",
    "TAB": "Tab",
    "BS": "BSpace",
    "ESC": "Escape",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "HOME": "Home",
    "END": "End",
    "PGUP": "PPage",
    "PGDN": "NPage",
    "C-L": "C-l",
    "C-D": "C-d",
    "C-C": "C-c",
}
_SHELL_PROMPT_TIMEOUT_SECONDS = 10


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
        env: Mapping[str, str] | None = None,
    ):
        super().__init__(work_dir, username)
        _install_libtmux_redaction_filter()
        self.PS1 = CmdOutputMetadata.to_ps1_prompt()
        self._env = normalize_terminal_env(env)

    def initialize(self) -> None:
        """Initialize the tmux terminal session."""
        if self._initialized:
            return

        env = build_terminal_env(self._env)
        # Disable interactive pagers (git, man, systemctl, ...) so commands that
        # auto-launch `less` on a TTY don't capture the pane and wedge the session.
        env.setdefault("GIT_PAGER", "cat")
        env.setdefault("PAGER", "cat")
        # Never reuse a socket: tmux numeric IDs restart at zero after server exit.
        self.server = libtmux.Server(
            socket_name=f"{TMUX_SOCKET_NAME}-{uuid.uuid4().hex}", environment=env
        )
        _shell_command = "/bin/bash"
        if self.username in ["root", "openhands"]:
            # This starts a non-login (new) shell for the given user
            _shell_command = f"su {self.username} -"

        window_command = _shell_command

        logger.debug(f"Initializing tmux terminal with command: {window_command}")
        session_name = f"openhands-{self.username}-{uuid.uuid4()}"
        self.session = self.server.new_session(
            session_name=session_name,
            start_directory=self.work_dir,
            kill_session=True,
            x=TMUX_SESSION_WIDTH,
            y=TMUX_SESSION_HEIGHT,
        )
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

        try:
            self._initialize_shell()
        except Exception:
            self.session.kill()
            raise

        logger.debug(f"Tmux terminal initialized with work dir: {self.work_dir}")
        self._initialized: bool = True

    def _initialize_shell(self) -> None:
        """Configure Bash and wait until its prompt is ready for input."""
        command = (
            f"set +H; export PROMPT_COMMAND='export PS1=\"{self.PS1}\"'; "
            'export PS2=""; clear'
        )
        self._run_shell_command_and_wait(command)
        self.pane.cmd("clear-history")

    def _run_shell_command_and_wait(self, command: str) -> None:
        marker_id = uuid.uuid4().hex
        marker_start = f"__OH_TMUX_READY_{marker_id}_"
        marker_end = "__"
        self.pane.send_keys(
            f'{command}; printf "%s%s\\n" "{marker_start}" "{marker_end}" > /dev/tty'
        )

        deadline = time.monotonic() + _SHELL_PROMPT_TIMEOUT_SECONDS
        marker = marker_start + marker_end
        while time.monotonic() < deadline:
            content = self._capture_pane(self.pane)
            marker_position = content.find(marker)
            if (
                marker_position >= 0
                and content.find(CMD_OUTPUT_PS1_END, marker_position + len(marker)) >= 0
            ):
                return
            time.sleep(0.01)

        raise RuntimeError("Timed out waiting for the tmux shell prompt")

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

        Supports:
          - Plain text (uses literal paste; preserves spaces/newlines)
          - Named specials: ENTER, TAB, BS, ESC, UP, DOWN, LEFT, RIGHT,
            HOME, END, PGUP, PGDN, C-L, C-D, C-C
          - Generic Ctrl sequences: C-a..C-z, CTRL-x, CTRL+x

        Args:
            text: Text or key sequence to send
            enter: Whether to send Enter key after the text.
                   Ignored for special/ctrl keys.
        """
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            raise RuntimeError("Tmux terminal is not initialized")

        # Map normalized names to tmux key names
        upper = text.strip().upper()

        # 1) Named specials
        if upper in _TMUX_SPECIALS:
            self.pane.send_keys(_TMUX_SPECIALS[upper], enter=False)
            return

        # 2) Generic Ctrl-<letter>
        ctrl = parse_ctrl_key(text)
        if ctrl is not None:
            self.pane.send_keys(ctrl, enter=False)
            return

        # 3) Plain text — use literal=True so tmux doesn't split on
        #    whitespace or interpret special tokens.
        self.pane.send_keys(text, enter=False, literal=True)
        if enter and not text.endswith("\n"):
            self.pane.send_keys("Enter", enter=False)

    @staticmethod
    def _capture_pane(pane: libtmux.Pane) -> str:
        return "\n".join(
            line.rstrip() for line in pane.cmd("capture-pane", "-J", "-pS", "-").stdout
        )

    def read_screen(self) -> str:
        """Read the current tmux pane content.

        Returns:
            Current visible content of the tmux pane
        """
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            raise RuntimeError("Tmux terminal is not initialized")

        return self._capture_pane(self.pane)

    def clear_screen(self) -> None:
        """Clear the tmux pane screen and history.

        We intentionally avoid sending ``C-l`` (Ctrl+L) because the form-feed
        control character (``^L``) can leak into the shell input buffer over SSH
        connections.

        Instead, we run the ``clear`` command to clear the visible screen, then
        use tmux's ``clear-history`` to remove the scrollback buffer.
        """
        if not self._initialized or not isinstance(self.pane, libtmux.Pane):
            raise RuntimeError("Tmux terminal is not initialized")

        self.pane.send_keys("clear", enter=True)
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
            return not content.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
        except Exception:
            return False
