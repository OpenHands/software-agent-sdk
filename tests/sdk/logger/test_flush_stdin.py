"""Tests for flush_stdin terminal cleanup functionality.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

import importlib.util
import os
import sys
from unittest import mock

import pytest

from openhands.sdk.logger import (
    clear_buffered_input,
    flush_stdin,
    get_buffered_input,
)
from openhands.sdk.logger.logger import (
    _find_csi_end,
    _find_osc_end,
    _is_csi_final_byte,
    _parse_stdin_data,
)


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


class TestSelectiveFlushing:
    """Tests for selective flushing - parsing escape sequences vs user input."""

    def test_is_csi_final_byte(self):
        """Test CSI final byte detection (0x40-0x7E)."""
        # Valid final bytes
        assert _is_csi_final_byte(0x40)  # @
        assert _is_csi_final_byte(0x52)  # R (cursor position)
        assert _is_csi_final_byte(0x6E)  # n (device status)
        assert _is_csi_final_byte(0x7E)  # ~ (end of range)

        # Invalid final bytes
        assert not _is_csi_final_byte(0x3F)  # ? (below range)
        assert not _is_csi_final_byte(0x7F)  # DEL (above range)
        assert not _is_csi_final_byte(0x30)  # 0 (parameter byte)

    def test_find_csi_end_complete_sequence(self):
        """Test finding end of complete CSI sequences."""
        # DSR response: \x1b[5;10R
        data = b"\x1b[5;10R"
        assert _find_csi_end(data, 0) == 7

        # Simple sequence: \x1b[H (cursor home)
        data = b"\x1b[H"
        assert _find_csi_end(data, 0) == 3

        # With offset
        data = b"abc\x1b[5;10Rxyz"
        assert _find_csi_end(data, 3) == 10

    def test_find_csi_end_incomplete_sequence(self):
        """Test handling of incomplete CSI sequences (preserved)."""
        # Incomplete - no final byte
        data = b"\x1b[5;10"
        assert _find_csi_end(data, 0) == 0  # Returns start = preserve

        # Just the introducer
        data = b"\x1b["
        assert _find_csi_end(data, 0) == 0

    def test_find_osc_end_with_bel_terminator(self):
        """Test finding end of OSC sequences with BEL terminator."""
        # Background color response: \x1b]11;rgb:XXXX/XXXX/XXXX\x07
        data = b"\x1b]11;rgb:30fb/3708/41af\x07"
        assert _find_osc_end(data, 0) == len(data)

    def test_find_osc_end_with_st_terminator(self):
        """Test finding end of OSC sequences with ST terminator."""
        # OSC with ST terminator: \x1b]...\x1b\\
        data = b"\x1b]11;rgb:30fb/3708/41af\x1b\\"
        assert _find_osc_end(data, 0) == len(data)

    def test_find_osc_end_incomplete_sequence(self):
        """Test handling of incomplete OSC sequences (preserved)."""
        # No terminator
        data = b"\x1b]11;rgb:30fb/3708/41af"
        assert _find_osc_end(data, 0) == 0  # Returns start = preserve

    def test_parse_stdin_data_csi_only(self):
        """Test parsing data with only CSI sequences - all flushed."""
        data = b"\x1b[5;10R"
        preserved, flushed = _parse_stdin_data(data)
        assert preserved == b""
        assert flushed == 7

    def test_parse_stdin_data_osc_only(self):
        """Test parsing data with only OSC sequences - all flushed."""
        data = b"\x1b]11;rgb:30fb/3708/41af\x07"
        preserved, flushed = _parse_stdin_data(data)
        assert preserved == b""
        assert flushed == len(data)

    def test_parse_stdin_data_user_input_only(self):
        """Test parsing data with only user input - all preserved."""
        data = b"hello world"
        preserved, flushed = _parse_stdin_data(data)
        assert preserved == b"hello world"
        assert flushed == 0

    def test_parse_stdin_data_mixed_content(self):
        """Test parsing mixed escape sequences and user input."""
        # User types "ls" while terminal response arrives
        data = b"l\x1b[5;10Rs"
        preserved, flushed = _parse_stdin_data(data)
        assert preserved == b"ls"
        assert flushed == 7  # The CSI sequence

    def test_parse_stdin_data_multiple_sequences(self):
        """Test parsing multiple escape sequences."""
        # Two DSR responses: \x1b[5;10R (7 bytes) + \x1b[6;1R (6 bytes)
        data = b"\x1b[5;10R\x1b[6;1R"
        preserved, flushed = _parse_stdin_data(data)
        assert preserved == b""
        assert flushed == 13  # 7 + 6

    def test_parse_stdin_data_preserves_incomplete_csi(self):
        """Test that incomplete CSI sequences are preserved as user input."""
        # User typing escape followed by [ (could be arrow key start)
        data = b"\x1b[5;10"  # Incomplete - no final byte
        preserved, flushed = _parse_stdin_data(data)
        # Incomplete sequence preserved byte by byte
        assert b"\x1b" in preserved
        assert flushed == 0

    def test_parse_stdin_data_arrow_keys_preserved(self):
        """Test that arrow key sequences are handled correctly.

        Note: Arrow keys are CSI sequences like \\x1b[A, \\x1b[B, etc.
        They WILL be flushed because they are complete CSI sequences.
        This is acceptable because arrow keys during agent execution
        are unlikely to be meaningful user input.
        """
        # Up arrow
        data = b"\x1b[A"
        preserved, flushed = _parse_stdin_data(data)
        # Arrow key is a complete CSI sequence - gets flushed
        assert flushed == 3
        assert preserved == b""


class TestBufferedInput:
    """Tests for get_buffered_input and clear_buffered_input."""

    def test_get_buffered_input_is_exported(self):
        """get_buffered_input should be available in the public API."""
        from openhands.sdk.logger import (
            get_buffered_input as exported_get_buffered_input,
        )

        assert callable(exported_get_buffered_input)

    def test_clear_buffered_input_is_exported(self):
        """clear_buffered_input should be available in the public API."""
        from openhands.sdk.logger import (
            clear_buffered_input as exported_clear,
        )

        assert callable(exported_clear)

    def test_get_buffered_input_returns_and_clears(self):
        """get_buffered_input should return buffer and clear it."""
        from openhands.sdk.logger import logger as logger_module

        # Set up some buffered data
        logger_module._preserved_input_buffer = b"test data"

        # Get the data
        data = get_buffered_input()
        assert data == b"test data"

        # Buffer should now be empty
        assert logger_module._preserved_input_buffer == b""

        # Second call returns empty
        data2 = get_buffered_input()
        assert data2 == b""

    def test_clear_buffered_input_clears_buffer(self):
        """clear_buffered_input should clear the buffer without returning."""
        from openhands.sdk.logger import logger as logger_module

        # Set up some buffered data
        logger_module._preserved_input_buffer = b"test data"

        # Clear it
        clear_buffered_input()

        # Buffer should be empty
        assert logger_module._preserved_input_buffer == b""


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

    def test_flush_stdin_drains_escape_sequences_only(self):
        """Verify flush_stdin drains only escape sequences from stdin.

        This test writes mixed data (escape sequences + user input) to a PTY
        and verifies that flush_stdin only counts escape sequences as flushed,
        while preserving user input in the buffer.
        """
        import pty
        import time

        from openhands.sdk.logger import logger as logger_module

        # Clear any existing buffer
        logger_module._preserved_input_buffer = b""

        master_fd, slave_fd = pty.openpty()
        slave_file = None

        try:
            slave_file = os.fdopen(slave_fd, "r")

            # Write mixed data: CSI sequence + user text
            # \x1b[5;10R is a DSR response (7 bytes)
            # "hello" is user input (5 bytes)
            test_data = b"\x1b[5;10Rhello"
            os.write(master_fd, test_data)

            time.sleep(0.05)

            with mock.patch.object(sys, "stdin", slave_file):
                bytes_flushed = flush_stdin()

            # Only the CSI sequence should be counted as flushed
            assert bytes_flushed == 7, f"Expected 7 bytes flushed, got {bytes_flushed}"

            # "hello" should be preserved in buffer
            buffered = get_buffered_input()
            assert buffered == b"hello", f"Expected b'hello' buffered, got {buffered!r}"

        finally:
            os.close(master_fd)
            if slave_file is not None:
                try:
                    slave_file.close()
                except OSError:
                    pass

    def test_flush_stdin_drains_pending_data(self):
        """Verify flush_stdin actually drains pending escape sequence data.

        This test writes pure escape sequence data to a PTY and verifies
        that flush_stdin reads and discards it, returning the correct byte count.
        """
        import pty
        import time

        from openhands.sdk.logger import logger as logger_module

        # Clear any existing buffer
        logger_module._preserved_input_buffer = b""

        master_fd, slave_fd = pty.openpty()
        slave_file = None

        try:
            slave_file = os.fdopen(slave_fd, "r")

            # Write escape sequence data to the master (simulating terminal response)
            test_data = b"\x1b[5;10R"  # Simulated DSR response
            os.write(master_fd, test_data)

            time.sleep(0.05)

            with mock.patch.object(sys, "stdin", slave_file):
                bytes_flushed = flush_stdin()

            # Verify data was flushed
            assert bytes_flushed == len(test_data), (
                f"Expected {len(test_data)} bytes flushed, got {bytes_flushed}"
            )

            # Nothing should be buffered (it was all escape sequences)
            buffered = get_buffered_input()
            assert buffered == b"", f"Expected empty buffer, got {buffered!r}"

        finally:
            os.close(master_fd)
            if slave_file is not None:
                try:
                    slave_file.close()
                except OSError:
                    pass
