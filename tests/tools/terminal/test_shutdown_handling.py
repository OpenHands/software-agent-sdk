"""Tests for shutdown handling in terminal sessions.

This module tests the shutdown handling logic that prevents ImportError
during Python shutdown when terminal sessions are being cleaned up.
"""

import shutil
import time
from contextlib import suppress
from unittest.mock import Mock

import pytest

from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


def _server_stays_up(server, timeout: float) -> bool:
    """True if *server* still answers after *timeout* seconds.

    ``Server.is_alive()`` distinguishes a live server with zero sessions
    from a dead one, so this also covers a server that outlives its last
    session (e.g. with ``exit-empty`` off).
    """
    deadline = time.monotonic() + timeout
    while server.is_alive():
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.05)
    return False


def test_tmux_terminal_close_normal_operation():
    """Test that TmuxTerminal.close() works normally."""
    terminal = TmuxTerminal("/tmp")

    # Manually set up a mock session to avoid complex initialization
    mock_session = Mock()
    terminal.session = mock_session

    # Normal close should call session.kill()
    terminal.close()

    mock_session.kill.assert_called_once()
    assert terminal.closed


def test_tmux_terminal_close_during_shutdown():
    """Test that TmuxTerminal.close() handles ImportError during shutdown."""
    terminal = TmuxTerminal("/tmp")

    # Manually set up a mock session to avoid complex initialization
    mock_session = Mock()
    mock_session.kill.side_effect = ImportError(
        "sys.meta_path is None, Python is likely shutting down"
    )
    terminal.session = mock_session

    # close() should handle the ImportError gracefully
    terminal.close()  # Should not raise an exception

    # session.kill() should have been called but raised ImportError
    mock_session.kill.assert_called_once()
    assert terminal.closed


def test_tmux_terminal_close_multiple_calls():
    """Test that multiple close() calls are safe."""
    terminal = TmuxTerminal("/tmp")

    # Manually set up a mock session to avoid complex initialization
    mock_session = Mock()
    terminal.session = mock_session

    # First close
    terminal.close()
    mock_session.kill.assert_called_once()

    # Second close should be safe and not call kill() again
    terminal.close()
    mock_session.kill.assert_called_once()  # Still only called once


def test_tmux_terminal_close_when_session_already_dead():
    """Test that TmuxTerminal.close() handles session already dead/killed externally."""
    terminal = TmuxTerminal("/tmp")

    # Manually set up a mock session to avoid complex initialization
    mock_session = Mock()
    # Simulate the "can't find session" error from tmux
    mock_session.kill.side_effect = Exception("can't find session: $2")
    terminal.session = mock_session

    # close() should handle the exception gracefully
    terminal.close()  # Should not raise an exception

    # session.kill() should have been called but raised an exception
    mock_session.kill.assert_called_once()
    assert terminal.closed


def test_tmux_terminal_close_before_initialize_is_safe():
    """close() must not require ``server``/``session`` to exist.

    ``__init__`` does not create the tmux session; that happens lazily in
    ``initialize()``. Closing a terminal that was never initialized (e.g.
    construction failed partway) must not raise ``AttributeError``.
    """
    terminal = TmuxTerminal("/tmp")

    terminal.close()  # Should not raise an exception

    assert terminal.closed


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires tmux")
def test_tmux_terminal_close_kills_its_own_dedicated_server():
    """TmuxTerminal owns a dedicated tmux server; close() must kill it too.

    With ``exit-empty`` off, a tmux server outlives its last session once
    that session is killed. ``close()`` must shut the server down as well,
    not merely the session, or it leaks one tmux process per close.
    """
    terminal = TmuxTerminal("/tmp")
    terminal.initialize()
    server = terminal.server

    try:
        # Real, standard tmux server option (tmux(1)); default is "on".
        # This is the terminal's own isolated server, never a shared one.
        result = server.cmd("set-option", "-g", "exit-empty", "off")
        assert not result.stderr
        assert _server_stays_up(server, timeout=0.2)

        terminal.close()

        assert not _server_stays_up(server, timeout=2.0), (
            "TmuxTerminal.close() left its dedicated tmux server running; "
            "it must kill the server, not just the session"
        )
    finally:
        # Belt-and-suspenders: never leave a test-owned server lingering.
        # Harmless (and silently ignored) if it is already dead.
        with suppress(Exception):
            server.kill()
