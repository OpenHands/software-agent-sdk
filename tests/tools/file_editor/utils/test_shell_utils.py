import subprocess
from unittest.mock import MagicMock, patch

import pytest

from openhands.tools.file_editor.utils.config import (
    MAX_RESPONSE_LEN_CHAR,
)
from openhands.tools.file_editor.utils.constants import (
    CONTENT_TRUNCATED_NOTICE,
)
from openhands.tools.file_editor.utils.shell import (
    check_tool_installed,
    run_shell_cmd,
)


def test_run_shell_cmd_success():
    """Test running a successful shell command."""
    import sys

    cmd = "echo 'Hello, World!'"
    returncode, stdout, stderr = run_shell_cmd(cmd)

    assert returncode == 0
    # PowerShell keeps quotes in output, bash removes them
    if sys.platform == "win32":
        assert "Hello, World!" in stdout
    else:
        assert stdout.strip() == "Hello, World!"
    assert stderr == ""


@patch("subprocess.Popen")
def test_run_shell_cmd_timeout(mock_popen):
    """Test that a TimeoutError is raised if command times out."""
    mock_process = MagicMock()
    mock_process.communicate.side_effect = subprocess.TimeoutExpired(
        cmd="sleep 2", timeout=1
    )
    mock_popen.return_value = mock_process

    with pytest.raises(TimeoutError, match="Command 'sleep 2' timed out"):
        run_shell_cmd("sleep 2", timeout=1)


@patch("subprocess.Popen")
def test_run_shell_cmd_truncation(mock_popen):
    """Test that stdout and stderr are truncated correctly."""
    long_output = "a" * (MAX_RESPONSE_LEN_CHAR + 10)
    mock_process = MagicMock()
    mock_process.communicate.return_value = (long_output, long_output)
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    returncode, stdout, stderr = run_shell_cmd("echo long_output")

    assert returncode == 0
    assert len(stdout) <= MAX_RESPONSE_LEN_CHAR + len(CONTENT_TRUNCATED_NOTICE)
    assert len(stderr) <= MAX_RESPONSE_LEN_CHAR + len(CONTENT_TRUNCATED_NOTICE)


def test_check_tool_installed_python():
    """Test check_tool_installed returns True for an installed tool (python)."""
    # 'python' is usually available if Python is installed
    assert check_tool_installed("python") is True


def test_check_tool_installed_nonexistent_tool():
    """Test check_tool_installed returns False for a nonexistent tool."""
    # Use a made-up tool name that is very unlikely to exist
    assert check_tool_installed("nonexistent_tool_xyz") is False
