"""Offline smoke for the three-file backport in the frozen 1.51.0 bundle.

Run only in a disposable container. Uses real tmux and no LLM.
"""
import importlib.metadata
import json
import tempfile
from unittest.mock import patch

from libtmux.exc import TmuxObjectDoesNotExist

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.terminal.terminal_session import TerminalSession
from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool
from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


results = {}
with tempfile.TemporaryDirectory() as work:
    pool = TmuxPanePool(work, max_panes=1)
    pool.initialize()
    try:
        with patch.object(TmuxTerminal, "clear_screen", side_effect=RuntimeError("setup")):
            for _ in range(3):
                try:
                    pool.checkout(timeout=0.1)
                except RuntimeError as exc:
                    assert str(exc) == "setup"
                else:
                    raise AssertionError("Expected setup failure")
        with pool.pane(timeout=0.1) as handle:
            handle.terminal.send_keys("echo recovered_capacity")
        results["capacity_after_repeated_setup_failure"] = True
        handle.terminal.server.cmd("kill-server")
        with pool.pane(timeout=0.1):
            pass
        results["no_tmux_lookup_from_pool_logging"] = True
    finally:
        pool.close()

    executor = TerminalExecutor(work, terminal_type="tmux")
    try:
        with patch.object(
            TerminalSession, "execute",
            side_effect=TmuxObjectDoesNotExist("Could not find object"),
        ):
            result = executor(TerminalAction(command="echo interrupted", timeout=5))
            assert result.is_error and "rebuilt the terminal pool" in result.text
        result = executor(TerminalAction(command="echo recovered", timeout=5))
        assert not result.is_error and result.exit_code == 0
        results["missing_object_recovery"] = True
        result = executor(TerminalAction(command="exit 7", timeout=1))
        assert result.is_error and "rebuilt the terminal pool" in result.text
        result = executor(TerminalAction(command="echo after_exit", timeout=5))
        assert not result.is_error and "after_exit" in result.text
        results["real_shell_exit_recovery"] = True
    finally:
        executor.close()
print(json.dumps({
    "versions": {name: importlib.metadata.version(name) for name in
                 ("openhands-sdk", "openhands-tools", "libtmux")},
    "results": results,
}, indent=2))
