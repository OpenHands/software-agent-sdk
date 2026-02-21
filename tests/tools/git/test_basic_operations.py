"""Tests for GitTool basic operations."""

import tempfile
from pathlib import Path

import pytest

from openhands.sdk.git.utils import run_git_command
from openhands.tools.git import GitAction, GitExecutor, GitObservation


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    run_git_command(["git", "init"], cwd=repo_path)
    run_git_command(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
    )
    run_git_command(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
    )

    return repo_path


@pytest.fixture
def git_executor(temp_repo):
    """Create a GitExecutor instance for testing."""
    return GitExecutor(working_dir=str(temp_repo))


def test_git_tool_initialization():
    """Test GitExecutor can be initialized."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GitExecutor(working_dir=temp_dir)
        assert executor.working_dir == Path(temp_dir)


def test_git_init(tmp_path):
    """Test git init command."""
    new_repo = tmp_path / "new_repo"
    executor = GitExecutor(working_dir=str(tmp_path))

    action = GitAction(command="init", repo_path=str(new_repo))
    obs = executor(action)

    assert isinstance(obs, GitObservation)
    assert obs.command == "init"
    assert obs.success is True
    assert not obs.is_error
    assert (new_repo / ".git").exists()


def test_git_status_empty_repo(git_executor, temp_repo):
    """Test git status on an empty repository."""
    action = GitAction(command="status")
    obs = git_executor(action)

    assert isinstance(obs, GitObservation)
    assert obs.command == "status"
    assert obs.success is True
    assert not obs.is_error


def test_git_add_and_commit(git_executor, temp_repo):
    """Test adding and committing files."""
    # Create a test file
    test_file = temp_repo / "test.txt"
    test_file.write_text("Hello, World!")

    # Add file
    add_action = GitAction(command="add", files=[str(test_file)])
    add_obs = git_executor(add_action)

    assert add_obs.command == "add"
    assert add_obs.success is True
    assert not add_obs.is_error

    # Commit
    commit_action = GitAction(command="commit", message="Initial commit")
    commit_obs = git_executor(commit_action)

    assert commit_obs.command == "commit"
    assert commit_obs.success is True
    assert not commit_obs.is_error


def test_git_add_all_files(git_executor, temp_repo):
    """Test adding all files with '.' pattern."""
    # Create multiple test files
    (temp_repo / "file1.txt").write_text("File 1")
    (temp_repo / "file2.txt").write_text("File 2")

    # Add all files
    add_action = GitAction(command="add", files=["."])
    add_obs = git_executor(add_action)

    assert add_obs.success is True
    assert not add_obs.is_error


def test_git_commit_without_message(git_executor):
    """Test that commit fails without a message."""
    action = GitAction(command="commit")
    obs = git_executor(action)

    assert obs.command == "commit"
    assert obs.success is False
    assert obs.is_error
    assert "required" in obs.text.lower()


def test_git_commit_with_all_changes(git_executor, temp_repo):
    """Test commit with all_changes flag."""
    # Create and commit initial file
    test_file = temp_repo / "test.txt"
    test_file.write_text("Initial content")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # Modify the file
    test_file.write_text("Modified content")

    # Commit with all_changes flag
    action = GitAction(
        command="commit",
        message="Update test.txt",
        all_changes=True,
    )
    obs = git_executor(action)

    assert obs.command == "commit"
    assert obs.success is True
    assert not obs.is_error


def test_git_branch_create(git_executor, temp_repo):
    """Test creating a new branch."""
    # Need at least one commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("test")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # Create branch
    action = GitAction(command="branch", branch_name="feature-branch")
    obs = git_executor(action)

    assert obs.command == "branch"
    assert obs.success is True
    assert "feature-branch" in obs.text


def test_git_branch_list(git_executor, temp_repo):
    """Test listing branches."""
    # Need at least one commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("test")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # List branches
    action = GitAction(command="branch")
    obs = git_executor(action)

    assert obs.command == "branch"
    assert obs.success is True
    assert "main" in obs.text or "master" in obs.text


def test_git_checkout_existing_branch(git_executor, temp_repo):
    """Test checking out an existing branch."""
    # Setup: create initial commit and branch
    test_file = temp_repo / "test.txt"
    test_file.write_text("test")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)
    run_git_command(["git", "branch", "feature"], cwd=temp_repo)

    # Checkout branch
    action = GitAction(command="checkout", branch_name="feature")
    obs = git_executor(action)

    assert obs.command == "checkout"
    assert obs.success is True
    assert "feature" in obs.text


def test_git_checkout_create_branch(git_executor, temp_repo):
    """Test creating and checking out a new branch."""
    # Setup: create initial commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("test")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # Checkout with create_branch
    action = GitAction(
        command="checkout",
        branch_name="new-feature",
        create_branch=True,
    )
    obs = git_executor(action)

    assert obs.command == "checkout"
    assert obs.success is True
    assert "new-feature" in obs.text


def test_git_checkout_without_branch_name(git_executor):
    """Test that checkout fails without branch name."""
    action = GitAction(command="checkout")
    obs = git_executor(action)

    assert obs.command == "checkout"
    assert obs.success is False
    assert obs.is_error


def test_git_diff_no_changes(git_executor, temp_repo):
    """Test diff when there are no changes."""
    # Setup: create initial commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("test")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # Check diff
    action = GitAction(command="diff")
    obs = git_executor(action)

    assert obs.command == "diff"
    assert obs.success is True
    assert "No differences" in obs.text or obs.text == ""


def test_git_diff_with_changes(git_executor, temp_repo):
    """Test diff when there are uncommitted changes."""
    # Setup: create initial commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("original")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # Modify file
    test_file.write_text("modified")

    # Check diff
    action = GitAction(command="diff")
    obs = git_executor(action)

    assert obs.command == "diff"
    assert obs.success is True
    assert "original" in obs.text or "modified" in obs.text


def test_git_log(git_executor, temp_repo):
    """Test viewing commit log."""
    # Setup: create some commits
    test_file = temp_repo / "test.txt"
    test_file.write_text("commit 1")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "First commit"], cwd=temp_repo)

    test_file.write_text("commit 2")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Second commit"], cwd=temp_repo)

    # View log
    action = GitAction(command="log", max_count=5)
    obs = git_executor(action)

    assert obs.command == "log"
    assert obs.success is True
    assert "First commit" in obs.text or "Second commit" in obs.text


def test_git_log_oneline(git_executor, temp_repo):
    """Test viewing commit log in oneline format."""
    # Setup: create a commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("test")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Test commit"], cwd=temp_repo)

    # View log
    action = GitAction(command="log", max_count=5, oneline=True)
    obs = git_executor(action)

    assert obs.command == "log"
    assert obs.success is True


def test_git_log_empty_repo(git_executor):
    """Test log on repository with no commits."""
    action = GitAction(command="log")
    obs = git_executor(action)

    assert obs.command == "log"
    assert obs.success is True


def test_git_stash_save(git_executor, temp_repo):
    """Test stashing changes."""
    # Setup: create initial commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("original")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # Make changes
    test_file.write_text("modified")

    # Stash changes
    action = GitAction(
        command="stash",
        stash_operation="save",
        stash_message="Test stash",
    )
    obs = git_executor(action)

    assert obs.command == "stash"
    assert obs.success is True


def test_git_stash_list(git_executor, temp_repo):
    """Test listing stashes."""
    # Setup: create initial commit
    test_file = temp_repo / "test.txt"
    test_file.write_text("original")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Initial"], cwd=temp_repo)

    # List stashes (empty)
    action = GitAction(command="stash", stash_operation="list")
    obs = git_executor(action)

    assert obs.command == "stash"
    assert obs.success is True


def test_git_reset_soft(git_executor, temp_repo):
    """Test soft reset."""
    # Setup: create commits
    test_file = temp_repo / "test.txt"
    test_file.write_text("commit 1")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "First"], cwd=temp_repo)

    test_file.write_text("commit 2")
    run_git_command(["git", "add", "."], cwd=temp_repo)
    run_git_command(["git", "commit", "-m", "Second"], cwd=temp_repo)

    # Reset to previous commit
    action = GitAction(
        command="reset",
        reset_mode="soft",
        reset_target="HEAD~1",
    )
    obs = git_executor(action)

    assert obs.command == "reset"
    assert obs.success is True


def test_git_remote_list(git_executor):
    """Test listing remotes."""
    action = GitAction(command="remote", remote_operation="list")
    obs = git_executor(action)

    assert obs.command == "remote"
    assert obs.success is True


def test_git_remote_add(git_executor):
    """Test adding a remote."""
    action = GitAction(
        command="remote",
        remote_operation="add",
        remote_name="origin",
        remote_url="https://github.com/test/repo.git",
    )
    obs = git_executor(action)

    assert obs.command == "remote"
    assert obs.success is True


def test_git_remote_add_without_url(git_executor):
    """Test that adding a remote fails without URL."""
    action = GitAction(
        command="remote",
        remote_operation="add",
        remote_name="origin",
    )
    obs = git_executor(action)

    assert obs.command == "remote"
    assert obs.success is False
    assert obs.is_error


def test_git_clone(tmp_path):
    """Test cloning a repository."""
    # This test would require a real repo or mock, skip for now
    # as it requires network access
    pass


def test_invalid_command(git_executor):
    """Test handling of invalid git command."""
    # This would require modifying GitAction to accept arbitrary strings
    # Current implementation uses Literal type which prevents this at validation
    pass


def test_git_operation_on_nonexistent_repo(tmp_path):
    """Test git operations on a non-git directory."""
    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()

    executor = GitExecutor(working_dir=str(non_repo))
    action = GitAction(command="status")
    obs = executor(action)

    assert obs.is_error is True
    assert obs.success is False
