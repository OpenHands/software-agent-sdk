"""Factory for creating appropriate terminal sessions based on system capabilities."""

import platform
import subprocess
import warnings
from typing import Literal

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env
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
            env=sanitized_env(),
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _get_powershell_command(explicit_shell_path: str | None = None) -> str | None:
    """Return a usable PowerShell executable for the current platform."""
    candidates = [explicit_shell_path] if explicit_shell_path else []
    if platform.system() == "Windows":
        candidates.extend(["powershell.exe", "pwsh.exe", "powershell", "pwsh"])
    else:
        candidates.extend(["pwsh"])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "-Command", "Write-Host 'PowerShell Available'"],
                capture_output=True,
                text=True,
                timeout=5.0,
                env=sanitized_env(),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if result.returncode == 0:
            return candidate
    return None


def _is_powershell_available() -> bool:
    """Check if PowerShell is available on the system."""
    return _get_powershell_command() is not None


def _create_windows_terminal(
    work_dir: str,
    username: str | None,
    no_change_timeout_seconds: int | None,
    shell_path: str | None,
) -> TerminalSession:
    from openhands.tools.terminal.terminal.windows_terminal import WindowsTerminal

    resolved_shell_path = _get_powershell_command(shell_path)
    if resolved_shell_path is None:
        raise RuntimeError("PowerShell is not available on this system")

    terminal = WindowsTerminal(work_dir, username, shell_path=resolved_shell_path)
    return TerminalSession(terminal, no_change_timeout_seconds)


def create_terminal_session(
    work_dir: str,
    username: str | None = None,
    no_change_timeout_seconds: int | None = None,
    terminal_type: Literal["tmux", "subprocess", "powershell"] | None = None,
    shell_path: str | None = None,
) -> TerminalSession:
    """Create an appropriate terminal session based on system capabilities.

    Args:
        work_dir: Working directory for the session
        username: Optional username for the session
        no_change_timeout_seconds: Timeout for no output change
        terminal_type: Force a specific session type ('tmux', 'subprocess',
            or 'powershell'). If None, auto-detect based on system capabilities.
        shell_path: Path to the shell binary. On Unix this is used for the
            subprocess backend; on Windows it can point to a PowerShell binary.

    Returns:
        TerminalSession instance

    Raises:
        RuntimeError: If the requested session type is not available
    """
    if terminal_type:
        if terminal_type == "tmux":
            if not _is_tmux_available():
                raise RuntimeError("Tmux is not available on this system")
            from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

            logger.info("Using forced TmuxTerminal")
            terminal = TmuxTerminal(work_dir, username)
            return TerminalSession(terminal, no_change_timeout_seconds)

        if terminal_type == "powershell":
            if platform.system() != "Windows":
                raise RuntimeError(
                    "PowerShell terminal backend is only supported on Windows"
                )
            logger.info("Using forced WindowsTerminal")
            return _create_windows_terminal(
                work_dir,
                username,
                no_change_timeout_seconds,
                shell_path,
            )

        if terminal_type == "subprocess":
            if platform.system() == "Windows":
                logger.info("Using WindowsTerminal for forced single-session backend")
                return _create_windows_terminal(
                    work_dir,
                    username,
                    no_change_timeout_seconds,
                    shell_path,
                )
            from openhands.tools.terminal.terminal.subprocess_terminal import (
                SubprocessTerminal,
            )

            logger.info("Using forced SubprocessTerminal")
            terminal = SubprocessTerminal(work_dir, username, shell_path)
            return TerminalSession(terminal, no_change_timeout_seconds)

        raise ValueError(f"Unknown session type: {terminal_type}")

    if platform.system() == "Windows":
        logger.info("Auto-detected: Using WindowsTerminal (PowerShell backend)")
        return _create_windows_terminal(
            work_dir,
            username,
            no_change_timeout_seconds,
            shell_path,
        )

    if _is_tmux_available():
        from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

        logger.info("Auto-detected: Using TmuxTerminal (tmux available)")
        terminal = TmuxTerminal(work_dir, username)
        return TerminalSession(terminal, no_change_timeout_seconds)

    from openhands.tools.terminal.terminal.subprocess_terminal import (
        SubprocessTerminal,
    )

    _tmux_warning = (
        "tmux is not installed. Falling back to subprocess-based terminal, "
        "which may be less stable. For best agent performance, install tmux "
        "(e.g. `apt-get install tmux` or `brew install tmux`)."
    )
    logger.warning(_tmux_warning)
    warnings.warn(_tmux_warning, stacklevel=2)
    terminal = SubprocessTerminal(work_dir, username, shell_path)
    return TerminalSession(terminal, no_change_timeout_seconds)
