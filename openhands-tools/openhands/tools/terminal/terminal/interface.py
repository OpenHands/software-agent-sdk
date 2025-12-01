"""Abstract interface for terminal backends."""

import os
from abc import ABC, abstractmethod

from openhands.tools.terminal.constants import (
    NO_CHANGE_TIMEOUT_SECONDS,
)
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)


class TerminalInterface(ABC):
    """Abstract interface for terminal backends.

    This interface abstracts the low-level terminal operations, allowing
    different backends (tmux, subprocess, PowerShell) to be used with
    the same high-level session controller logic.
    """

    work_dir: str
    username: str | None
    _initialized: bool
    _closed: bool

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
    ):
        """Initialize the terminal interface.

        Args:
            work_dir: Working directory for the terminal
            username: Optional username for the terminal session
        """
        self.work_dir = work_dir
        self.username = username
        self._initialized = False
        self._closed = False

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the terminal backend.

        This should set up the terminal session, configure the shell,
        and prepare it for command execution. Implementations should
        set self._initialized = True upon successful initialization.
        """

    @abstractmethod
    def close(self) -> None:
        """Clean up the terminal backend.

        This should properly terminate the terminal session and
        clean up any resources. Implementations should set
        self._closed = True upon successful cleanup.
        """

    @abstractmethod
    def send_keys(self, text: str, enter: bool = True) -> None:
        """Send text/keys to the terminal.

        Args:
            text: Text or key sequence to send to the terminal.
            enter: Whether to send Enter key after the text. Defaults to True.
        """

    @abstractmethod
    def read_screen(self) -> str:
        """Read the current terminal screen content.

        Returns:
            Current visible content of the terminal screen as a string.
        """

    @abstractmethod
    def clear_screen(self) -> None:
        """Clear the terminal screen and history.

        This method should clear both the visible terminal screen content
        and any scrollback history, providing a clean slate for new output.
        """

    @abstractmethod
    def interrupt(self) -> bool:
        """Send interrupt signal (Ctrl+C) to the terminal.

        This method should send a SIGINT signal to interrupt any currently
        running command in the terminal session.

        Returns:
            True if interrupt was sent successfully, False otherwise.
        """

    @abstractmethod
    def is_running(self) -> bool:
        """Check if a command is currently running in the terminal.

        This method should determine whether there is an active command
        execution in progress in the terminal session.

        Returns:
            True if a command is running, False otherwise.
        """

    @property
    def initialized(self) -> bool:
        """Check if the terminal is initialized."""
        return self._initialized

    @property
    def closed(self) -> bool:
        """Check if the terminal is closed."""
        return self._closed

    def is_powershell(self) -> bool:
        """Check if this is a PowerShell terminal.

        Returns:
            True if this is a PowerShell terminal, False otherwise
        """
        return False


class TerminalSessionBase(ABC):
    """Abstract base class for terminal sessions.

    This class defines the common interface for all terminal session implementations,
    including tmux-based, subprocess-based, and PowerShell-based sessions.
    """

    work_dir: str
    username: str | None
    no_change_timeout_seconds: int
    _initialized: bool
    _closed: bool
    _cwd: str

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        no_change_timeout_seconds: int | None = None,
    ):
        """Initialize the terminal session.

        Args:
            work_dir: Working directory for the session
            username: Optional username for the session
            no_change_timeout_seconds: Timeout for no output change
        """
        self.work_dir = work_dir
        self.username = username
        self.no_change_timeout_seconds = (
            no_change_timeout_seconds or NO_CHANGE_TIMEOUT_SECONDS
        )
        self._initialized = False
        self._closed = False
        self._cwd = os.path.abspath(work_dir)

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the terminal session.

        This method should set up the terminal session, configure the environment,
        and prepare it for command execution. Implementations should set
        self._initialized = True upon successful initialization.
        """

    @abstractmethod
    def execute(self, action: TerminalAction) -> TerminalObservation:
        """Execute a command in the terminal session.

        This method should execute the bash command specified in the action
        and return the results including output, exit code, and any errors.

        Args:
            action: The bash action to execute containing the command and parameters.

        Returns:
            TerminalObservation with the command result including output,
            exit code, and execution metadata.
        """

    @abstractmethod
    def close(self) -> None:
        """Clean up the terminal session.

        This method should properly terminate the terminal session, clean up
        any resources, and set self._closed = True upon successful cleanup.
        """

    @abstractmethod
    def interrupt(self) -> bool:
        """Interrupt the currently running command (equivalent to Ctrl+C).

        This method should send a SIGINT signal to interrupt any currently
        running command in the terminal session.

        Returns:
            True if interrupt was successful, False otherwise.
        """

    @abstractmethod
    def is_running(self) -> bool:
        """Check if a command is currently running.

        This method should determine whether there is an active command
        execution in progress in the terminal session.

        Returns:
            True if a command is running, False otherwise.
        """

    @property
    def cwd(self) -> str:
        """Get the current working directory."""
        return self._cwd

    def __del__(self) -> None:
        """Ensure the session is closed when the object is destroyed."""
        try:
            self.close()
        except ImportError:
            # Python is shutting down, let the OS handle cleanup
            pass
