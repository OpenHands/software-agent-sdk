"""
Tests for Windows terminal implementation.

This test suite specifically tests the WindowsTerminal backend functionality
on Windows systems. Tests are skipped on non-Windows platforms.
"""

import os
import platform
import tempfile
import time

import pytest

from openhands.tools.terminal.definition import ExecuteBashAction
from openhands.tools.terminal.terminal import create_terminal_session


# Skip all tests in this file if not on Windows
pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="Windows terminal tests only run on Windows",
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def windows_session(temp_dir):
    """Create a WindowsTerminal session for testing."""
    session = create_terminal_session(work_dir=temp_dir)
    session.initialize()
    yield session
    session.close()


def test_windows_terminal_initialization(temp_dir):
    """Test that WindowsTerminal initializes correctly."""
    session = create_terminal_session(work_dir=temp_dir)
    assert session is not None
    assert not session.terminal.initialized

    session.initialize()
    assert session.terminal.initialized
    assert not session.terminal.closed

    session.close()
    assert session.terminal.closed


def test_windows_terminal_basic_command(windows_session):
    """Test executing a basic command."""
    obs = windows_session.execute(ExecuteBashAction(command="echo Hello"))

    assert obs.output is not None
    assert "Hello" in obs.output
    assert obs.exit_code == 0


def test_windows_terminal_pwd(windows_session, temp_dir):
    """Test that Get-Location returns correct working directory."""
    obs = windows_session.execute(ExecuteBashAction(command="(Get-Location).Path"))

    # PowerShell may show the path in different format
    # Verify the command executed and returned the working directory
    assert obs.output is not None
    assert obs.exit_code == 0
    assert temp_dir.lower().replace("\\", "/") in obs.output.lower().replace("\\", "/")


def test_windows_terminal_cd_command(windows_session, temp_dir):
    """Test changing directory."""
    # Create a subdirectory
    test_dir = os.path.join(temp_dir, "testdir")
    os.makedirs(test_dir, exist_ok=True)

    # Change to the new directory
    obs = windows_session.execute(ExecuteBashAction(command=f"cd {test_dir}"))
    assert obs.exit_code == 0

    # Verify we're in the new directory
    # PowerShell uses Get-Location, not pwd
    obs = windows_session.execute(ExecuteBashAction(command="(Get-Location).Path"))
    # PowerShell may return path with different separators
    normalized_output = obs.output.replace("\\", "/").lower()
    normalized_test_dir = test_dir.replace("\\", "/").lower()
    assert normalized_test_dir in normalized_output


def test_windows_terminal_multiline_output(windows_session):
    """Test command with multiline output."""
    obs = windows_session.execute(
        ExecuteBashAction(command='echo "Line1"; echo "Line2"; echo "Line3"')
    )

    assert obs.output is not None
    assert "Line1" in obs.output
    assert "Line2" in obs.output
    assert "Line3" in obs.output


def test_windows_terminal_file_operations(windows_session, temp_dir):
    """Test file creation and reading."""
    test_file = os.path.join(temp_dir, "test.txt")

    # Create a file
    obs = windows_session.execute(
        ExecuteBashAction(command=f'echo "Test content" > "{test_file}"')
    )
    assert obs.exit_code == 0

    # Verify file was created
    assert os.path.exists(test_file)

    # Read the file
    obs = windows_session.execute(
        ExecuteBashAction(command=f'Get-Content "{test_file}"')
    )
    assert "Test content" in obs.output


def test_windows_terminal_error_handling(windows_session):
    """Test handling of commands that fail."""
    # Try to access a non-existent file
    obs = windows_session.execute(
        ExecuteBashAction(command='Get-Content "nonexistent_file.txt"')
    )

    # Command should fail (non-zero exit code or error in output)
    assert obs.exit_code != 0 or "cannot find" in obs.output.lower()


def test_windows_terminal_environment_variables(windows_session):
    """Test setting and reading environment variables."""
    # Set an environment variable
    obs = windows_session.execute(
        ExecuteBashAction(command='$env:TEST_VAR = "test_value"')
    )
    assert obs.exit_code == 0

    # Read the environment variable
    obs = windows_session.execute(ExecuteBashAction(command="echo $env:TEST_VAR"))
    assert "test_value" in obs.output


def test_windows_terminal_long_running_command(windows_session):
    """Test a command that takes some time to execute."""
    # Sleep for 2 seconds
    obs = windows_session.execute(
        ExecuteBashAction(command="Start-Sleep -Seconds 2; echo Done")
    )

    assert "Done" in obs.output
    assert obs.exit_code == 0


def test_windows_terminal_special_characters(windows_session):
    """Test handling of special characters in output."""
    obs = windows_session.execute(
        ExecuteBashAction(command='echo "Test@#$%^&*()_+-=[]{}|;:,.<>?"')
    )

    assert obs.output is not None
    assert obs.exit_code == 0


def test_windows_terminal_multiple_commands(windows_session):
    """Test executing multiple commands in sequence."""
    commands = [
        "echo First",
        "echo Second",
        "echo Third",
    ]

    for cmd in commands:
        obs = windows_session.execute(ExecuteBashAction(command=cmd))
        assert obs.exit_code == 0


def test_windows_terminal_send_keys(temp_dir):
    """Test send_keys method."""
    session = create_terminal_session(work_dir=temp_dir)
    session.initialize()

    # Send a command using send_keys
    session.terminal.send_keys("echo TestSendKeys", enter=True)
    time.sleep(0.5)

    # Read the output
    output = session.terminal.read_screen()
    assert output is not None

    session.close()


def test_windows_terminal_clear_screen(windows_session):
    """Test clear_screen method."""
    # Execute some commands
    windows_session.execute(ExecuteBashAction(command="echo Test1"))
    windows_session.execute(ExecuteBashAction(command="echo Test2"))

    # Clear the screen
    windows_session.terminal.clear_screen()

    # Execute another command
    obs = windows_session.execute(ExecuteBashAction(command="echo Test3"))
    assert "Test3" in obs.output


def test_windows_terminal_is_running(windows_session):
    """Test is_running method."""
    # Terminal should not be running a command initially
    assert not windows_session.terminal.is_running()

    # After executing a quick command, it should complete
    windows_session.execute(ExecuteBashAction(command="echo Quick"))
    assert not windows_session.terminal.is_running()


def test_windows_terminal_is_powershell(windows_session):
    """Test that is_powershell returns True for Windows terminal."""
    assert windows_session.terminal.is_powershell()


def test_windows_terminal_close_and_reopen(temp_dir):
    """Test closing and reopening a terminal session."""
    # Create and initialize first session
    session1 = create_terminal_session(work_dir=temp_dir)
    session1.initialize()

    obs = session1.execute(ExecuteBashAction(command="echo Session1"))
    assert "Session1" in obs.text

    # Close first session
    session1.close()
    assert session1.terminal.closed

    # Create and initialize second session
    session2 = create_terminal_session(work_dir=temp_dir)
    session2.initialize()

    obs = session2.execute(ExecuteBashAction(command="echo Session2"))
    assert "Session2" in obs.text

    session2.close()


def test_windows_terminal_timeout_handling(windows_session):
    """Test that very long commands respect timeout settings."""
    # This test might take a while, so we use a shorter timeout
    # Note: The actual timeout behavior depends on implementation
    obs = windows_session.execute(
        ExecuteBashAction(command="Start-Sleep -Seconds 1; echo Done")
    )

    # Should complete within reasonable time
    assert obs.output is not None


def test_windows_terminal_consecutive_commands(windows_session, temp_dir):
    """Test executing consecutive commands that depend on each other."""
    test_file = os.path.join(temp_dir, "counter.txt")

    # Create file with initial value
    obs1 = windows_session.execute(
        ExecuteBashAction(command=f'echo "1" > "{test_file}"')
    )
    assert obs1.exit_code == 0

    # Read and verify
    obs2 = windows_session.execute(
        ExecuteBashAction(command=f'Get-Content "{test_file}"')
    )
    assert "1" in obs2.output

    # Update the file
    obs3 = windows_session.execute(
        ExecuteBashAction(command=f'echo "2" > "{test_file}"')
    )
    assert obs3.exit_code == 0

    # Read and verify update
    obs4 = windows_session.execute(
        ExecuteBashAction(command=f'Get-Content "{test_file}"')
    )
    assert "2" in obs4.output


def test_windows_terminal_unicode_handling(windows_session):
    """Test handling of Unicode characters."""
    obs = windows_session.execute(ExecuteBashAction(command='echo "Hello 世界 🌍"'))

    # Just verify the command executes without crashing
    assert obs.output is not None


def test_windows_terminal_path_with_spaces(windows_session, temp_dir):
    """Test handling paths with spaces."""
    # Create directory with spaces in name
    dir_with_spaces = os.path.join(temp_dir, "test dir with spaces")
    os.makedirs(dir_with_spaces, exist_ok=True)

    # Create a file in that directory
    test_file = os.path.join(dir_with_spaces, "test.txt")
    obs = windows_session.execute(
        ExecuteBashAction(command=f'echo "Content" > "{test_file}"')
    )
    assert obs.exit_code == 0

    # Verify file exists
    assert os.path.exists(test_file)


def test_windows_terminal_command_with_quotes(windows_session):
    """Test command with various quote types."""
    obs = windows_session.execute(
        ExecuteBashAction(command="echo \"Double quotes\" ; echo 'Single quotes'")
    )

    assert obs.output is not None
    assert obs.exit_code == 0


def test_windows_terminal_empty_command(windows_session):
    """Test executing an empty command."""
    obs = windows_session.execute(ExecuteBashAction(command=""))

    # Empty command should execute without error
    assert obs.output is not None


def test_windows_terminal_working_directory_persistence(windows_session, temp_dir):
    """Test that working directory persists across commands."""
    # Create subdirectories
    dir1 = os.path.join(temp_dir, "dir1")
    dir2 = os.path.join(temp_dir, "dir2")
    os.makedirs(dir1, exist_ok=True)
    os.makedirs(dir2, exist_ok=True)

    # Change to dir1
    obs = windows_session.execute(ExecuteBashAction(command=f"cd '{dir1}'"))
    assert obs.exit_code == 0

    # Create file in current directory (should be dir1)
    obs = windows_session.execute(
        ExecuteBashAction(command='echo "In dir1" > file1.txt')
    )
    assert obs.exit_code == 0

    # Verify file was created in dir1
    assert os.path.exists(os.path.join(dir1, "file1.txt"))
