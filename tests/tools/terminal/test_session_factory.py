"""Tests for session factory and auto-detection logic."""

import tempfile
from unittest.mock import patch

import pytest

from openhands.tools.terminal.terminal import (
    SubprocessTerminal,
    TerminalSession,
    TmuxTerminal,
)
from openhands.tools.terminal.terminal.factory import (
    _is_tmux_available,
    create_terminal_session,
)


def test_tmux_detection():
    """Test tmux availability detection."""
    # This will depend on the test environment
    result = _is_tmux_available()
    assert isinstance(result, bool)


def test_forced_terminal_types():
    """Test forcing specific session types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Test forced subprocess session
        session = create_terminal_session(work_dir=temp_dir, terminal_type="subprocess")
        assert isinstance(session, TerminalSession)
        assert isinstance(session.terminal, SubprocessTerminal)
        session.close()

        # Test forced tmux session (if available)
        if _is_tmux_available():
            session = create_terminal_session(work_dir=temp_dir, terminal_type="tmux")
            assert isinstance(session, TerminalSession)
            assert isinstance(session.terminal, TmuxTerminal)
            session.close()


def test_invalid_terminal_type():
    """Test error handling for invalid session types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with pytest.raises(ValueError, match="Unknown session type"):
            create_terminal_session(work_dir=temp_dir, terminal_type="invalid")  # type: ignore


def test_unavailable_terminal_type():
    """Test error handling when requested session type is unavailable."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock tmux as unavailable
        with patch(
            "openhands.tools.terminal.terminal.factory._is_tmux_available",
            return_value=False,
        ):
            with pytest.raises(RuntimeError, match="Tmux is not available"):
                create_terminal_session(work_dir=temp_dir, terminal_type="tmux")


@patch("platform.system")
def test_auto_detection_unix(mock_system):
    """Test auto-detection on Unix-like systems."""
    mock_system.return_value = "Linux"

    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock tmux as available
        with patch(
            "openhands.tools.terminal.terminal.factory._is_tmux_available",
            return_value=True,
        ):
            session = create_terminal_session(work_dir=temp_dir)
            assert isinstance(session, TerminalSession)
            assert isinstance(session.terminal, TmuxTerminal)
            session.close()

        # Mock tmux as unavailable
        with patch(
            "openhands.tools.terminal.terminal.factory._is_tmux_available",
            return_value=False,
        ):
            session = create_terminal_session(work_dir=temp_dir)
            assert isinstance(session, TerminalSession)
            assert isinstance(session.terminal, SubprocessTerminal)
            session.close()


def test_session_parameters_passed():
    """Test that session parameters are properly passed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir,
            username="testuser",
            no_change_timeout_seconds=60,
            terminal_type="subprocess",
        )

        assert isinstance(session, TerminalSession)
        assert session.work_dir == temp_dir
        assert session.username == "testuser"
        assert session.no_change_timeout_seconds == 60
        # Check terminal parameters too
        assert session.terminal.work_dir == temp_dir
        assert session.terminal.username == "testuser"
        session.close()
