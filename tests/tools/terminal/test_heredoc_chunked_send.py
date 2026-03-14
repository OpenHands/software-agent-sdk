"""Tests for the heredoc chunked sending fix (GitHub issue #2181).

This tests that long multi-line commands (like heredocs) are sent line-by-line
to avoid overwhelming the PTY input buffer on macOS.
"""

import tempfile
import time

import pytest

from openhands.tools.terminal.terminal.subprocess_terminal import SubprocessTerminal


@pytest.fixture
def terminal():
    """Create a SubprocessTerminal for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        term = SubprocessTerminal(work_dir=tmpdir)
        term.initialize()
        # Allow time for initialization
        time.sleep(1)
        yield term
        term.close()


def create_heredoc_command(num_lines: int) -> str:
    """Create a heredoc command with the specified number of lines."""
    lines = [f"print('Line {i}')" for i in range(num_lines)]
    script = "\n".join(lines)
    return f"""cat > /tmp/test_script.py << 'EOF'
{script}
EOF
python3 /tmp/test_script.py"""


def test_short_heredoc_works(terminal: SubprocessTerminal):
    """Test that short heredocs (under threshold) work."""
    terminal.clear_screen()
    time.sleep(0.1)

    # 5 lines is well under the threshold
    cmd = create_heredoc_command(5)
    terminal.send_keys(cmd)

    # Wait for completion
    start_time = time.time()
    while terminal.is_running() and time.time() - start_time < 10:
        time.sleep(0.1)

    output = terminal.read_screen()
    assert "Line 4" in output


def test_long_heredoc_works(terminal: SubprocessTerminal):
    """Test that long heredocs (over threshold) work with chunked sending."""
    terminal.clear_screen()
    time.sleep(0.1)

    # 50 lines is over the _MULTILINE_THRESHOLD of 20
    cmd = create_heredoc_command(50)
    terminal.send_keys(cmd)

    # Wait for completion
    start_time = time.time()
    while terminal.is_running() and time.time() - start_time < 30:
        time.sleep(0.1)

    output = terminal.read_screen()
    assert "Line 49" in output


def test_very_long_heredoc_works(terminal: SubprocessTerminal):
    """Test that very long heredocs work with chunked sending."""
    terminal.clear_screen()
    time.sleep(0.1)

    # 100 lines - this would hang without the fix
    cmd = create_heredoc_command(100)
    terminal.send_keys(cmd)

    # Wait for completion
    start_time = time.time()
    while terminal.is_running() and time.time() - start_time < 60:
        time.sleep(0.1)

    output = terminal.read_screen()
    assert "Line 99" in output


def test_multiline_threshold_boundary(terminal: SubprocessTerminal):
    """Test behavior at the threshold boundary."""
    terminal.clear_screen()
    time.sleep(0.1)

    # Exactly at threshold (20 lines) - should use normal path
    cmd = create_heredoc_command(20)
    terminal.send_keys(cmd)

    start_time = time.time()
    while terminal.is_running() and time.time() - start_time < 15:
        time.sleep(0.1)

    output = terminal.read_screen()
    assert "Line 19" in output

    # One over threshold (21 lines) - should use chunked path
    terminal.clear_screen()
    time.sleep(0.1)

    cmd = create_heredoc_command(21)
    terminal.send_keys(cmd)

    start_time = time.time()
    while terminal.is_running() and time.time() - start_time < 15:
        time.sleep(0.1)

    output = terminal.read_screen()
    assert "Line 20" in output


def test_special_keys_not_affected_by_chunking():
    """Test that special keys like C-c are not affected by multiline logic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        term = SubprocessTerminal(work_dir=tmpdir)
        term.initialize()
        time.sleep(1)

        try:
            # Start a long-running command
            term.send_keys("sleep 60")
            time.sleep(0.5)

            # Send Ctrl-C - this should work immediately
            term.send_keys("C-c")
            time.sleep(0.5)

            # Verify the terminal is still responsive by checking we can read output
            screen = term.read_screen()
            assert len(screen) > 0  # Terminal should still be functional

            # Verify that a simple command works after Ctrl-C
            term.send_keys("echo 'test_complete'")
            time.sleep(0.5)
            screen = term.read_screen()
            assert "test_complete" in screen
        finally:
            term.close()
