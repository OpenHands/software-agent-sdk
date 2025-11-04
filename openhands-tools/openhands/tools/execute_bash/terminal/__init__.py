import platform

from openhands.tools.execute_bash.terminal.factory import create_terminal_session
from openhands.tools.execute_bash.terminal.interface import (
    TerminalInterface,
    TerminalSessionBase,
)
from openhands.tools.execute_bash.terminal.terminal_session import (
    TerminalCommandStatus,
    TerminalSession,
)

# Conditionally import platform-specific terminals
if platform.system() == "Windows":
    from openhands.tools.execute_bash.terminal.windows_terminal import WindowsTerminal
    __all__ = [
        "TerminalInterface",
        "TerminalSessionBase",
        "WindowsTerminal",
        "TerminalSession",
        "TerminalCommandStatus",
        "create_terminal_session",
    ]
else:
    from openhands.tools.execute_bash.terminal.subprocess_terminal import SubprocessTerminal
    from openhands.tools.execute_bash.terminal.tmux_terminal import TmuxTerminal
    __all__ = [
        "TerminalInterface",
        "TerminalSessionBase",
        "TmuxTerminal",
        "SubprocessTerminal",
        "TerminalSession",
        "TerminalCommandStatus",
        "create_terminal_session",
    ]
