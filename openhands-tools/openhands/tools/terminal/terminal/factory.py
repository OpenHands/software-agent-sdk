"""Factory for creating appropriate terminal sessions based on system capabilities."""

import platform
import subprocess
from typing import Literal

from openhands.sdk.logger import get_logger
from openhands.tools.terminal.terminal.terminal_session import TerminalSession


logger = get_logger(__name__)


def _is_tmux_available() -> bool:
    """Check if tmux is available on the system."""
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _is_powershell_available() -> bool:
    """Check if PowerShell is available on the system."""
    if platform.system() == "Windows":
        # Check for Windows PowerShell
        powershell_cmd = "powershell"
    else:
        # Check for PowerShell Core (pwsh) on non-Windows systems
        powershell_cmd = "pwsh"

    try:
        result = subprocess.run(
            [powershell_cmd, "-Command", "Write-Host 'PowerShell Available'"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def create_terminal_session(
    work_dir: str,
    username: str | None = None,
    no_change_timeout_seconds: int | None = None,
    terminal_type: Literal["tmux", "subprocess"] | None = None,
    shell_path: str | None = None,
) -> TerminalSession:
    """Create an appropriate terminal session based on system capabilities.

    Args:
        work_dir: Working directory for the session
        username: Optional username for the session
        no_change_timeout_seconds: Timeout for no output change
        terminal_type: Force a specific session type ('tmux', 'subprocess')
                     If None, auto-detect based on system capabilities
        shell_path: Path to the shell binary (for subprocess terminal type only).
                   If None, will auto-detect bash from PATH.

    Returns:
        TerminalSession instance

    Raises:
        RuntimeError: If the requested session type is not available
    """
    from openhands.tools.terminal.terminal.terminal_session import (
        TerminalSession,
    )

    if terminal_type:
        # Force specific session type
        if terminal_type == "tmux":
            if not _is_tmux_available():
                raise RuntimeError("Tmux is not available on this system")
            from openhands.tools.terminal.terminal.tmux_terminal import (
                TmuxTerminal,
            )

            logger.info("Using forced TmuxTerminal")
            terminal = TmuxTerminal(work_dir, username)
            return TerminalSession(terminal, no_change_timeout_seconds)
        elif terminal_type == "subprocess":
            from openhands.tools.terminal.terminal.subprocess_terminal import (
                SubprocessTerminal,
            )

            logger.info("Using forced SubprocessTerminal")
            terminal = SubprocessTerminal(work_dir, username, shell_path)
            return TerminalSession(terminal, no_change_timeout_seconds)
        else:
            raise ValueError(f"Unknown session type: {terminal_type}")

    # Auto-detect based on system capabilities
    system = platform.system()

    if system == "Windows":
        raise NotImplementedError("Windows is not supported yet for OpenHands V1.")
    else:
        # On Unix-like systems, prefer tmux if available, otherwise use subprocess
        if _is_tmux_available():
            from openhands.tools.terminal.terminal.tmux_terminal import (
                TmuxTerminal,
            )

            logger.info("Auto-detected: Using TmuxTerminal (tmux available)")
            terminal = TmuxTerminal(work_dir, username)
            return TerminalSession(terminal, no_change_timeout_seconds)
        else:
            from openhands.tools.terminal.terminal.subprocess_terminal import (
                SubprocessTerminal,
            )

            logger.info("Auto-detected: Using SubprocessTerminal (tmux not available)")
            terminal = SubprocessTerminal(work_dir, username, shell_path)
            return TerminalSession(terminal, no_change_timeout_seconds)
