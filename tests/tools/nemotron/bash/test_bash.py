"""Tests for Nemotron bash tool."""

from openhands.tools.nemotron.bash.definition import BashAction, BashTool
from openhands.tools.nemotron.bash.impl import BashExecutor


def test_bash_tool_name():
    """Test that BashTool has the correct name."""
    assert BashTool.name == "bash"


def test_bash_basic_command(tmp_path):
    """Test basic command execution."""
    executor = BashExecutor(working_dir=str(tmp_path))
    action = BashAction(command="echo hello")
    obs = executor(action)

    assert not obs.is_error
    assert "hello" in obs.text


def test_bash_exit_code(tmp_path):
    """Test that exit codes are captured."""
    executor = BashExecutor(working_dir=str(tmp_path))

    # Successful command
    action = BashAction(command="true")
    obs = executor(action)
    assert obs.exit_code == 0

    # Failing command - use a subshell to avoid killing the session
    action = BashAction(command="(exit 42)")
    obs = executor(action)
    assert obs.exit_code == 42


def test_bash_working_directory(tmp_path):
    """Test that working directory is set correctly."""
    executor = BashExecutor(working_dir=str(tmp_path))
    action = BashAction(command="pwd")
    obs = executor(action)

    assert str(tmp_path) in obs.text


def test_bash_environment_persistence(tmp_path):
    """Test that environment variables persist across commands."""
    executor = BashExecutor(working_dir=str(tmp_path))

    # Set environment variable
    action = BashAction(command="export MY_VAR=test123")
    executor(action)

    # Check it persists
    action = BashAction(command="echo $MY_VAR")
    obs = executor(action)

    assert "test123" in obs.text


def test_bash_file_operations(tmp_path):
    """Test file operations through bash."""
    executor = BashExecutor(working_dir=str(tmp_path))

    # Create a file
    action = BashAction(command="echo 'test content' > test.txt")
    executor(action)

    # Read it back
    action = BashAction(command="cat test.txt")
    obs = executor(action)

    assert "test content" in obs.text

    # Verify file exists
    assert (tmp_path / "test.txt").exists()


def test_bash_action_visualize():
    """Test BashAction visualization."""
    action = BashAction(command="ls -la")
    viz = action.visualize

    assert "$ " in viz.plain
    assert "ls -la" in viz.plain


def test_bash_executor_close(tmp_path):
    """Test that executor can be closed without errors."""
    executor = BashExecutor(working_dir=str(tmp_path))
    executor.close()
    # Should not raise
