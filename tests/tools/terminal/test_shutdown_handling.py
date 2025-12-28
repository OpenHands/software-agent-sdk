"""Tests for shutdown handling in terminal sessions.

This module tests the shutdown handling logic that prevents ImportError
during Python shutdown when terminal sessions are being cleaned up.
"""

from unittest.mock import Mock

from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


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
