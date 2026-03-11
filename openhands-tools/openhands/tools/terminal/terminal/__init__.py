import platform
from typing import TYPE_CHECKING

from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    TerminalSessionBase,
)
from openhands.tools.terminal.terminal.terminal_session import (
    TerminalCommandStatus,
    TerminalSession,
)


# These backends depend on Unix-only modules (fcntl, pty, libtmux)
if platform.system() != "Windows":
    from openhands.tools.terminal.terminal.subprocess_terminal import (
        SubprocessTerminal,
    )
    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

if TYPE_CHECKING:
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
