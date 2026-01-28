import platform

from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    TerminalSessionBase,
)
from openhands.tools.terminal.terminal.terminal_session import (
    TerminalCommandStatus,
    TerminalSession,
)


# Conditionally import platform-specific terminals
if platform.system() == "Windows":
    from openhands.tools.terminal.terminal.windows_terminal import WindowsTerminal

    __all__ = [
        "TerminalInterface",
        "TerminalSessionBase",
        "WindowsTerminal",
        "TerminalSession",
        "TerminalCommandStatus",
        "create_terminal_session",
    ]
else:
    from openhands.tools.terminal.terminal.subprocess_terminal import (
        SubprocessTerminal,
    )
    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

    __all__ = [
        "TerminalInterface",
        "TerminalSessionBase",
        "TmuxTerminal",
        "SubprocessTerminal",
        "TerminalSession",
        "TerminalCommandStatus",
        "create_terminal_session",
    ]
