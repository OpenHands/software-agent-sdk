import sys

from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    TerminalSessionBase,
)
from openhands.tools.terminal.terminal.terminal_session import (
    TerminalCommandStatus,
    TerminalSession,
)

__all__ = [
    "TerminalInterface",
    "TerminalSessionBase",
    "TerminalSession",
    "TerminalCommandStatus",
    "create_terminal_session",
]

# SubprocessTerminal and TmuxTerminal use Unix-only modules (fcntl, pty)
# Only import them on Unix-like systems
if sys.platform != "win32":
    from openhands.tools.terminal.terminal.subprocess_terminal import (
        SubprocessTerminal,
    )
    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

    __all__.extend(["TmuxTerminal", "SubprocessTerminal"])
