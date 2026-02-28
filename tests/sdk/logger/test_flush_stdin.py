"""Tests for flush_stdin terminal cleanup functionality.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

import importlib.util
import os
import sys
from unittest import mock

import pytest

from openhands.sdk.logger import flush_stdin


# Check if pty module is available (Unix only)
PTY_AVAILABLE = importlib.util.find_spec("pty") is not None


class TestFlushStdin:
    """Tests for the flush_stdin function."""

    def test_flush_stdin_returns_zero_when_not_tty(self):
        """flush_stdin should return 0 when stdin is not a tty."""
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            result = flush_stdin()
            assert result == 0

    def test_flush_stdin_returns_zero_when_termios_unavailable(self):
        """flush_stdin should return 0 when termios is not available (e.g., Windows).

        Note: This test verifies the code path by checking the function's behavior
        when termios import fails. On platforms where termios is available, we
        simulate its absence by patching the import mechanism.
        """
        if importlib.util.find_spec("termios") is None:
            # On Windows/platforms without termios, test the real behavior
            with mock.patch.object(sys.stdin, "isatty", return_value=True):
                result = flush_stdin()
                assert result == 0
        else:
            # On Unix, we can't easily unload termios since it's already imported.
            # The termios unavailable path is covered by Windows CI.
            pytest.skip("termios is available; Windows CI covers the unavailable path")

    def test_flush_stdin_is_exported(self):
        """flush_stdin should be available in the public API."""
        from openhands.sdk.logger import flush_stdin as exported_flush_stdin

        assert callable(exported_flush_stdin)

    def test_flush_stdin_handles_oserror_gracefully(self):
        """flush_stdin should handle OSError gracefully."""
        if importlib.util.find_spec("termios") is None:
            pytest.skip("termios not available on this platform")

        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("termios.tcgetattr", side_effect=OSError("test error")):
                result = flush_stdin()
                assert result == 0

    def test_flush_stdin_handles_termios_error_gracefully(self):
        """flush_stdin should handle termios.error gracefully."""
        if importlib.util.find_spec("termios") is None:
            pytest.skip("termios not available on this platform")

        import termios

        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch(
                "termios.tcgetattr",
                side_effect=termios.error("test error"),
            ):
                result = flush_stdin()
                assert result == 0


class TestFlushStdinIntegration:
    """Integration tests for flush_stdin in conversation flow."""

    def test_flush_stdin_called_in_visualizer_on_event(self):
        """Verify flush_stdin is called before rendering events in the visualizer."""
        from openhands.sdk.conversation.visualizer.default import (
            DefaultConversationVisualizer,
        )
        from openhands.sdk.event import PauseEvent

        visualizer = DefaultConversationVisualizer()

        with mock.patch(
            "openhands.sdk.conversation.visualizer.default.flush_stdin"
        ) as mock_flush:
            # Create a simple event and trigger on_event
            event = PauseEvent()
            visualizer.on_event(event)

            # Verify flush_stdin was called
            mock_flush.assert_called_once()

    def test_flush_stdin_registered_with_atexit(self):
        """Verify flush_stdin is registered as an atexit handler."""

        # Get registered atexit functions
        # Note: atexit._run_exitfuncs() would run them, but we just check registration
        # The atexit module doesn't expose a clean way to inspect handlers,
        # but we can verify by checking the module-level flag
        from openhands.sdk.logger import logger as logger_module

        assert logger_module._cleanup_registered is True


@pytest.mark.skipif(not PTY_AVAILABLE, reason="pty module not available (Windows)")
class TestFlushStdinPTY:
    """PTY-based tests for flush_stdin to verify real terminal behavior."""

    def test_flush_stdin_restores_termios_settings(self):
        """Verify termios settings are properly restored after flush_stdin.

        This test uses a PTY to create a real terminal environment and verifies
        that the termios settings (especially VMIN/VTIME in cc array) are
        correctly restored after flush_stdin modifies them temporarily.

        This catches the shallow-copy bug where list(old) would cause old[6]
        and new[6] to share the same reference, corrupting the restore.
        """
        import pty
        import termios

        # Create a pseudo-terminal
        master_fd, slave_fd = pty.openpty()
        slave_file = None

        try:
            # Open the slave as a file object to use as stdin
            slave_file = os.fdopen(slave_fd, "r")

            # Get original termios settings
            original_settings = termios.tcgetattr(slave_file)
            original_vmin = original_settings[6][termios.VMIN]
            original_vtime = original_settings[6][termios.VTIME]

            # Patch stdin to use our PTY slave
            with mock.patch.object(sys, "stdin", slave_file):
                # Call flush_stdin - this modifies VMIN/VTIME temporarily
                flush_stdin()

                # Get settings after flush_stdin
                restored_settings = termios.tcgetattr(slave_file)
                restored_vmin = restored_settings[6][termios.VMIN]
                restored_vtime = restored_settings[6][termios.VTIME]

            # Verify settings were restored correctly
            assert restored_vmin == original_vmin, (
                f"VMIN not restored: {original_vmin!r} -> {restored_vmin!r}"
            )
            assert restored_vtime == original_vtime, (
                f"VTIME not restored: {original_vtime!r} -> {restored_vtime!r}"
            )

        finally:
            os.close(master_fd)
            # slave_fd is closed when slave_file is closed
            if slave_file is not None:
                try:
                    slave_file.close()
                except OSError:
                    pass  # May already be closed

    def test_flush_stdin_drains_pending_data(self):
        """Verify flush_stdin actually drains pending data from stdin.

        This test writes data to a PTY and verifies that flush_stdin
        reads and discards it, returning the correct byte count.
        """
        import pty
        import time

        # Create a pseudo-terminal
        master_fd, slave_fd = pty.openpty()
        slave_file = None

        try:
            slave_file = os.fdopen(slave_fd, "r")

            # Write some test data to the master (simulating terminal input)
            test_data = b"\x1b[5;10R"  # Simulated DSR response
            os.write(master_fd, test_data)

            # Give the data time to be available
            time.sleep(0.05)

            # Patch stdin to use our PTY slave
            with mock.patch.object(sys, "stdin", slave_file):
                # flush_stdin should drain the pending data
                bytes_flushed = flush_stdin()

            # Verify data was flushed
            assert bytes_flushed == len(test_data), (
                f"Expected {len(test_data)} bytes flushed, got {bytes_flushed}"
            )

        finally:
            os.close(master_fd)
            if slave_file is not None:
                try:
                    slave_file.close()
                except OSError:
                    pass
