"""Tests for git exceptions module."""

import pytest

from openhands.sdk.git.exceptions import (
    GitCommandError,
    GitError,
    GitPathError,
    GitRepositoryError,
)


def test_git_error_base_exception():
    """Test GitError is the base exception."""
    error = GitError("Base error message")
    assert isinstance(error, Exception)
    assert str(error) == "Base error message"


def test_git_error_inheritance():
    """Test that all git exceptions inherit from GitError."""
    assert issubclass(GitRepositoryError, GitError)
    assert issubclass(GitCommandError, GitError)
    assert issubclass(GitPathError, GitError)


def test_git_repository_error_basic():
    """Test GitRepositoryError with basic message."""
    error = GitRepositoryError("Repository not found")
    assert str(error) == "Repository not found"
    assert error.command is None
    assert error.exit_code is None


def test_git_repository_error_with_command():
    """Test GitRepositoryError with command attribute."""
    error = GitRepositoryError("Git command failed", command="git status")
    assert str(error) == "Git command failed"
    assert error.command == "git status"
    assert error.exit_code is None


def test_git_repository_error_with_exit_code():
    """Test GitRepositoryError with exit code attribute."""
    error = GitRepositoryError("Git command failed", exit_code=128)
    assert str(error) == "Git command failed"
    assert error.command is None
    assert error.exit_code == 128


def test_git_repository_error_with_all_attributes():
    """Test GitRepositoryError with all attributes."""
    error = GitRepositoryError(
        "Git command failed", command="git clone", exit_code=1
    )
    assert str(error) == "Git command failed"
    assert error.command == "git clone"
    assert error.exit_code == 1


def test_git_command_error_basic():
    """Test GitCommandError with required attributes."""
    error = GitCommandError(
        "Command execution failed",
        command=["git", "status"],
        exit_code=1,
    )
    assert str(error) == "Command execution failed"
    assert error.command == ["git", "status"]
    assert error.exit_code == 1
    assert error.stderr == ""


def test_git_command_error_with_stderr():
    """Test GitCommandError with stderr attribute."""
    stderr_output = "fatal: not a git repository"
    error = GitCommandError(
        "Command failed",
        command=["git", "log"],
        exit_code=128,
        stderr=stderr_output,
    )
    assert str(error) == "Command failed"
    assert error.command == ["git", "log"]
    assert error.exit_code == 128
    assert error.stderr == stderr_output


def test_git_command_error_with_empty_command():
    """Test GitCommandError with empty command list."""
    error = GitCommandError(
        "No command provided",
        command=[],
        exit_code=-1,
    )
    assert error.command == []
    assert error.exit_code == -1


def test_git_command_error_with_complex_command():
    """Test GitCommandError with complex command."""
    command = ["git", "commit", "-m", "Test message", "--author=Test <test@example.com>"]
    error = GitCommandError(
        "Commit failed",
        command=command,
        exit_code=1,
        stderr="nothing to commit",
    )
    assert error.command == command
    assert len(error.command) == 5
    assert error.stderr == "nothing to commit"


def test_git_path_error_basic():
    """Test GitPathError with basic message."""
    error = GitPathError("File not found: /path/to/file")
    assert str(error) == "File not found: /path/to/file"
    assert isinstance(error, GitError)


def test_git_path_error_with_special_characters():
    """Test GitPathError with special characters in path."""
    error = GitPathError("Invalid path: /tmp/test file with spaces.txt")
    assert "test file with spaces" in str(error)


def test_exception_can_be_raised_and_caught():
    """Test that exceptions can be raised and caught properly."""
    with pytest.raises(GitError):
        raise GitError("Test error")

    with pytest.raises(GitRepositoryError):
        raise GitRepositoryError("Repository error")

    with pytest.raises(GitCommandError):
        raise GitCommandError("Command error", command=["git"], exit_code=1)

    with pytest.raises(GitPathError):
        raise GitPathError("Path error")


def test_exception_catch_as_base_class():
    """Test that specific exceptions can be caught as GitError."""
    with pytest.raises(GitError):
        raise GitRepositoryError("Repository error")

    with pytest.raises(GitError):
        raise GitCommandError("Command error", command=["git"], exit_code=1)

    with pytest.raises(GitError):
        raise GitPathError("Path error")


def test_git_command_error_preserves_command_list():
    """Test that GitCommandError doesn't modify the command list."""
    original_command = ["git", "diff", "--name-status"]
    error = GitCommandError(
        "Diff failed",
        command=original_command,
        exit_code=1,
    )
    assert error.command == original_command
    assert error.command is not original_command  # Should be independent


def test_git_repository_error_none_values():
    """Test GitRepositoryError with explicit None values."""
    error = GitRepositoryError("Error", command=None, exit_code=None)
    assert error.command is None
    assert error.exit_code is None


def test_exception_message_types():
    """Test that exception messages can be various string types."""
    # Regular string
    error1 = GitError("Regular message")
    assert str(error1) == "Regular message"

    # Empty string
    error2 = GitError("")
    assert str(error2) == ""

    # Multiline string
    error3 = GitError("Line 1\nLine 2\nLine 3")
    assert "Line 1" in str(error3)
    assert "Line 2" in str(error3)


def test_git_command_error_negative_exit_codes():
    """Test GitCommandError with negative exit codes (e.g., timeout)."""
    error = GitCommandError(
        "Command timed out",
        command=["git", "fetch"],
        exit_code=-1,
        stderr="Timeout expired",
    )
    assert error.exit_code == -1
    assert error.stderr == "Timeout expired"


def test_git_command_error_large_exit_code():
    """Test GitCommandError with large exit code."""
    error = GitCommandError(
        "Command failed",
        command=["git", "push"],
        exit_code=255,
    )
    assert error.exit_code == 255
