"""Tests for flush_stdin terminal cleanup functionality.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

import importlib.util
import sys
from unittest import mock

import pytest

from openhands.sdk.logger import flush_stdin


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
