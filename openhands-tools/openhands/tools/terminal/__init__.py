# Core tool interface
from openhands.tools.terminal.definition import (
    ExecuteBashAction,
    ExecuteBashObservation,
    TerminalAction,
    TerminalObservation,
    TerminalTool,
)
from openhands.tools.terminal.impl import BashExecutor, TerminalExecutor

# Terminal session architecture - import from sessions package
from openhands.tools.terminal.terminal import (
    TerminalCommandStatus,
    TerminalSession,
    create_terminal_session,
)


__all__ = [
    # === Core Tool Interface ===
    "TerminalTool",
    "TerminalAction",
    "TerminalObservation",
    "TerminalExecutor",
    # === Deprecated Aliases (backward compatibility) ===
    "ExecuteBashAction",
    "ExecuteBashObservation",
    "BashExecutor",
    # === Terminal Session Architecture ===
    "TerminalSession",
    "TerminalCommandStatus",
    "TerminalSession",
    "create_terminal_session",
]
