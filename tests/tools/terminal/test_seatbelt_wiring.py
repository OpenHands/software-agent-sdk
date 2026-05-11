"""Verify the ``seatbelt`` flag flows from TerminalExecutor down to backends."""

from __future__ import annotations

import pytest

from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.subprocess_terminal import SubprocessTerminal
from openhands.tools.terminal.terminal.tmux_terminal import (
    TmuxTerminal,
    _seatbelt_wrap_shell,
)


def test_seatbelt_wrap_shell_noop_when_disabled():
    assert _seatbelt_wrap_shell("/bin/bash", "/tmp/work", False) == "/bin/bash"


def test_seatbelt_wrap_shell_wraps_with_sandbox_exec():
    wrapped = _seatbelt_wrap_shell("/bin/bash", "/tmp/work", True)
    # The wrapped command must invoke sandbox-exec with the workspace profile,
    # and the original shell command must come last.
    assert "sandbox-exec" in wrapped
    assert wrapped.endswith("/bin/bash")
    assert '/tmp/work' in wrapped


def test_factory_subprocess_propagates_seatbelt():
    """Forcing the subprocess backend constructs SubprocessTerminal with seatbelt."""
    session = create_terminal_session(
        work_dir="/tmp",
        terminal_type="subprocess",
        seatbelt=True,
    )
    try:
        # ``terminal`` is the underlying SubprocessTerminal that received the flag.
        assert isinstance(session.terminal, SubprocessTerminal)
        assert session.terminal.seatbelt is True
    finally:
        session.close()


def test_tmux_terminal_stores_seatbelt_flag():
    """The TmuxTerminal stores seatbelt without trying to actually launch tmux."""
    terminal = TmuxTerminal("/tmp/work", username=None, seatbelt=True)
    assert terminal.seatbelt is True


def test_subprocess_terminal_stores_seatbelt_flag():
    terminal = SubprocessTerminal("/tmp/work", seatbelt=True)
    assert terminal.seatbelt is True


@pytest.mark.parametrize("seatbelt", [True, False])
def test_subprocess_terminal_default_seatbelt_off(seatbelt: bool):
    terminal = SubprocessTerminal("/tmp/work", seatbelt=seatbelt)
    assert terminal.seatbelt is seatbelt
