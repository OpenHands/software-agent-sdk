import platform

from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.interface import (
    SUPPORTED_SPECIAL_KEYS,
    TerminalInterface,
    TerminalSessionBase,
    parse_ctrl_key,
)
from openhands.tools.terminal.terminal.terminal_session import (
    TerminalCommandStatus,
    TerminalSession,
)


if platform.system() == "Windows":
    from openhands.tools.terminal.terminal.windows_terminal import WindowsTerminal

    __all__ = [
        "SUPPORTED_SPECIAL_KEYS",
        "TerminalInterface",
        "TerminalSessionBase",
        "TerminalSession",
        "TerminalCommandStatus",
        "WindowsTerminal",
        "create_terminal_session",
        "parse_ctrl_key",
    ]
else:
    from openhands.tools.terminal.terminal.subprocess_terminal import (
        SubprocessTerminal,
    )
    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

    __all__ = [
        "SUPPORTED_SPECIAL_KEYS",
        "TerminalInterface",
        "TerminalSessionBase",
        "TerminalSession",
        "TerminalCommandStatus",
        "TmuxTerminal",
        "SubprocessTerminal",
        "create_terminal_session",
        "parse_ctrl_key",
    ]
