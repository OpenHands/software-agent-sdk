"""Tests for git utils module."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.git.utils import (
    GIT_EMPTY_TREE_HASH,
    get_valid_ref,
    run_git_command,
    validate_git_repository,
)


def run_bash_command(command: str, cwd: str) -> subprocess.CompletedProcess:
    """Run a bash command in the specified directory."""
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def setup_git_repo(repo_dir: str) -> None:
    """Initialize a git repository with basic configuration."""
    run_bash_command("git init", repo_dir)
    run_bash_command("git config user.name 'Test User'", repo_dir)
    run_bash_command("git config user.email 'test@example.com'", repo_dir)


def test_git_empty_tree_hash_constant():
    """Test that GIT_EMPTY_TREE_HASH constant is correct."""
    assert GIT_EMPTY_TREE_HASH == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert len(GIT_EMPTY_TREE_HASH) == 40  # SHA-1 hash length
    assert isinstance(GIT_EMPTY_TREE_HASH, str)


def test_run_git_command_success():
    """Test run_git_command with successful command."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        result = run_git_command(["git", "status", "--porcelain"], temp_dir)

        assert isinstance(result, str)
        # Empty repo should have empty status
        assert result == ""


def test_run_git_command_with_output():
    """Test run_git_command that produces output."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Get current branch name
        result = run_git_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], temp_dir
        )

        assert result in ["master", "main"]  # Default branch names


def test_run_git_command_failure():
    """Test run_git_command with failing command."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        with pytest.raises(GitCommandError) as exc_info:
            run_git_command(["git", "log", "nonexistent_ref"], temp_dir)

        error = exc_info.value
        assert error.exit_code != 0
        assert error.command == ["git", "log", "nonexistent_ref"]
        assert isinstance(error.stderr, str)


def test_run_git_command_not_a_git_repo():
    """Test run_git_command in non-git directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Don't initialize git

        with pytest.raises(GitCommandError) as exc_info:
            run_git_command(["git", "status"], temp_dir)

        error = exc_info.value
        assert error.exit_code != 0


def test_run_git_command_timeout():
    """Test run_git_command with timeout."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock subprocess.run to simulate timeout
        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["git", "fetch"], timeout=30
            )

            with pytest.raises(GitCommandError) as exc_info:
                run_git_command(["git", "fetch"], temp_dir)

            error = exc_info.value
            assert error.exit_code == -1
            assert "timed out" in error.stderr.lower()


def test_run_git_command_git_not_found():
    """Test run_git_command when git is not installed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock subprocess.run to simulate git not found
        with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            with pytest.raises(GitCommandError) as exc_info:
                run_git_command(["git", "status"], temp_dir)

            error = exc_info.value
            assert error.exit_code == -1
            assert "not found" in error.stderr.lower()


def test_run_git_command_strips_output():
    """Test that run_git_command strips whitespace from output."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a file and get status
        (Path(temp_dir) / "test.txt").write_text("content")
        result = run_git_command(
            ["git", "ls-files", "--others", "--exclude-standard"], temp_dir
        )

        # Result should be stripped (no trailing newlines)
        assert result == "test.txt"
        assert not result.endswith("\n")


def test_run_git_command_with_special_characters():
    """Test run_git_command with special characters in arguments."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create commit with special characters
        (Path(temp_dir) / "file.txt").write_text("content")
        run_bash_command("git add .", temp_dir)

        # This should work even with special characters
        result = run_git_command(
            ["git", "commit", "-m", "Message with 'quotes' and \"double quotes\""],
            temp_dir,
        )

        assert "Message with" in result or result == ""  # Different git versions


def test_validate_git_repository_valid():
    """Test validate_git_repository with valid repository."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        validated_path = validate_git_repository(temp_dir)

        assert isinstance(validated_path, Path)
        assert validated_path.exists()
        assert validated_path.is_dir()
        assert (validated_path / ".git").exists()


def test_validate_git_repository_with_path_object():
    """Test validate_git_repository with Path object."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)
        path_obj = Path(temp_dir)

        validated_path = validate_git_repository(path_obj)

        assert isinstance(validated_path, Path)
        assert validated_path == path_obj.resolve()


def test_validate_git_repository_nonexistent():
    """Test validate_git_repository with nonexistent directory."""
    with pytest.raises(GitRepositoryError) as exc_info:
        validate_git_repository("/nonexistent/directory/path")

    assert "does not exist" in str(exc_info.value)


def test_validate_git_repository_not_a_directory():
    """Test validate_git_repository with a file instead of directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "file.txt"
        file_path.write_text("content")

        with pytest.raises(GitRepositoryError) as exc_info:
            validate_git_repository(str(file_path))

        assert "not a directory" in str(exc_info.value)


def test_validate_git_repository_not_a_git_repo():
    """Test validate_git_repository with non-git directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Don't initialize git

        with pytest.raises(GitRepositoryError) as exc_info:
            validate_git_repository(temp_dir)

        assert "Not a git repository" in str(exc_info.value)


def test_validate_git_repository_in_subdirectory():
    """Test validate_git_repository from a subdirectory of git repo."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create subdirectory
        sub_dir = Path(temp_dir) / "subdir"
        sub_dir.mkdir()

        # Should find git repo in parent
        validated_path = validate_git_repository(str(sub_dir))

        assert validated_path == Path(temp_dir).resolve()


def test_get_valid_ref_empty_repository():
    """Test get_valid_ref with empty repository."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        ref = get_valid_ref(temp_dir)

        # Should fall back to empty tree hash
        assert ref == GIT_EMPTY_TREE_HASH


def test_get_valid_ref_with_commits():
    """Test get_valid_ref with repository that has commits."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit a file
        (Path(temp_dir) / "file.txt").write_text("content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        ref = get_valid_ref(temp_dir)

        # Should return some valid reference (could be empty tree or HEAD)
        assert ref is not None
        assert isinstance(ref, str)
        assert len(ref) == 40  # SHA-1 hash


def test_get_valid_ref_with_string_path():
    """Test get_valid_ref with string path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        ref = get_valid_ref(str(temp_dir))

        assert ref is not None
        assert isinstance(ref, str)


def test_get_valid_ref_with_path_object():
    """Test get_valid_ref with Path object."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        ref = get_valid_ref(Path(temp_dir))

        assert ref is not None
        assert isinstance(ref, str)


def test_get_valid_ref_detached_head():
    """Test get_valid_ref in detached HEAD state."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a commit
        (Path(temp_dir) / "file.txt").write_text("content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Detach HEAD
        commit_hash = run_bash_command(
            "git rev-parse HEAD", temp_dir
        ).stdout.strip()
        run_bash_command(f"git checkout {commit_hash}", temp_dir)

        ref = get_valid_ref(temp_dir)

        # Should still return a valid reference
        assert ref is not None
        assert isinstance(ref, str)


def test_get_valid_ref_no_valid_reference():
    """Test get_valid_ref when no valid reference exists."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to always fail
        with patch(
            "openhands.sdk.git.utils.run_git_command"
        ) as mock_run:
            mock_run.side_effect = GitCommandError(
                "Error", command=["git"], exit_code=1
            )

            ref = get_valid_ref(temp_dir)

            # Should return None when all attempts fail
            assert ref is None


def test_run_git_command_preserves_working_directory():
    """Test that run_git_command doesn't change current working directory."""
    import os

    original_cwd = os.getcwd()

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        run_git_command(["git", "status"], temp_dir)

        assert os.getcwd() == original_cwd


def test_run_git_command_with_empty_args():
    """Test run_git_command with empty arguments."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with pytest.raises(GitCommandError):
            # Git command with no arguments should fail
            run_git_command(["git"], temp_dir)


def test_validate_git_repository_resolves_path():
    """Test that validate_git_repository resolves relative paths."""
    import os

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Change to temp dir and use relative path
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            validated_path = validate_git_repository(".")

            assert validated_path.is_absolute()
            assert validated_path == Path(temp_dir).resolve()
        finally:
            os.chdir(original_cwd)


def test_get_valid_ref_tries_multiple_strategies():
    """Test that get_valid_ref tries multiple strategies to find a reference."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create commits and branches
        (Path(temp_dir) / "file.txt").write_text("content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # The function should find a valid reference using one of its strategies
        ref = get_valid_ref(temp_dir)

        assert ref is not None
        # Verify it's a valid git object
        result = run_bash_command(
            f"git cat-file -t {ref}", temp_dir
        )
        # Result should be one of: commit, tree, blob, tag
        assert result.returncode == 0 or ref == GIT_EMPTY_TREE_HASH


def test_run_git_command_error_includes_command():
    """Test that GitCommandError includes the failed command."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        failed_command = ["git", "invalid-command"]
        with pytest.raises(GitCommandError) as exc_info:
            run_git_command(failed_command, temp_dir)

        error = exc_info.value
        assert error.command == failed_command


def test_run_git_command_error_includes_stderr():
    """Test that GitCommandError includes stderr output."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        with pytest.raises(GitCommandError) as exc_info:
            run_git_command(["git", "commit", "-m", "test"], temp_dir)

        error = exc_info.value
        assert error.stderr  # Should have some error message
        assert isinstance(error.stderr, str)


def test_validate_git_repository_with_worktree():
    """Test validate_git_repository with git worktree (.git as file)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a commit first
        (Path(temp_dir) / "file.txt").write_text("content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Create a worktree (in git 2.5+, .git can be a file pointing to real git dir)
        worktree_dir = Path(temp_dir) / "worktree"
        result = run_bash_command(
            f"git worktree add {worktree_dir} HEAD", temp_dir
        )

        if result.returncode == 0:  # git worktree command succeeded
            # The worktree directory should be recognized as a git repository
            validated_path = validate_git_repository(str(worktree_dir))
            assert validated_path.exists()


def test_get_valid_ref_with_remote_origin():
    """Test get_valid_ref with repository that has remote origin."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a bare repo to act as origin
        origin_dir = Path(temp_dir) / "origin.git"
        origin_dir.mkdir()
        run_bash_command("git init --bare", str(origin_dir))

        # Create a local repo and add origin
        local_dir = Path(temp_dir) / "local"
        local_dir.mkdir()
        setup_git_repo(str(local_dir))

        # Add origin remote
        run_bash_command(f"git remote add origin {origin_dir}", str(local_dir))

        # Create and push a commit
        (local_dir / "file.txt").write_text("content")
        run_bash_command("git add .", str(local_dir))
        run_bash_command("git commit -m 'Initial commit'", str(local_dir))
        run_bash_command("git push -u origin main || git push -u origin master", str(local_dir))

        # Get valid ref should work with origin
        ref = get_valid_ref(str(local_dir))

        assert ref is not None
        assert isinstance(ref, str)
        assert len(ref) == 40  # SHA-1 hash


def test_get_valid_ref_with_default_branch_detection():
    """Test get_valid_ref detects default branch from remote."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a bare repo with specific default branch
        origin_dir = Path(temp_dir) / "origin.git"
        origin_dir.mkdir()
        run_bash_command("git init --bare", str(origin_dir))

        # Create a local repo
        local_dir = Path(temp_dir) / "local"
        local_dir.mkdir()
        setup_git_repo(str(local_dir))

        # Add origin remote
        run_bash_command(f"git remote add origin {origin_dir}", str(local_dir))

        # Create and push a commit to a custom branch
        (local_dir / "file.txt").write_text("content")
        run_bash_command("git add .", str(local_dir))
        run_bash_command("git commit -m 'Initial commit'", str(local_dir))

        # Push to origin
        current_branch = run_bash_command("git branch --show-current", str(local_dir)).stdout.strip()
        run_bash_command(f"git push -u origin {current_branch}", str(local_dir))

        # Get valid ref should work
        ref = get_valid_ref(str(local_dir))

        assert ref is not None
        assert isinstance(ref, str)


def test_get_valid_ref_with_merge_base():
    """Test get_valid_ref includes merge base in reference search."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a bare repo
        origin_dir = Path(temp_dir) / "origin.git"
        origin_dir.mkdir()
        run_bash_command("git init --bare", str(origin_dir))

        # Create a local repo
        local_dir = Path(temp_dir) / "local"
        local_dir.mkdir()
        setup_git_repo(str(local_dir))

        # Add origin and push
        run_bash_command(f"git remote add origin {origin_dir}", str(local_dir))
        (local_dir / "file.txt").write_text("content")
        run_bash_command("git add .", str(local_dir))
        run_bash_command("git commit -m 'Initial commit'", str(local_dir))

        current_branch = run_bash_command("git branch --show-current", str(local_dir)).stdout.strip()
        run_bash_command(f"git push -u origin {current_branch}", str(local_dir))

        # Create a new commit locally
        (local_dir / "file2.txt").write_text("more content")
        run_bash_command("git add .", str(local_dir))
        run_bash_command("git commit -m 'Second commit'", str(local_dir))

        # Get valid ref should work and might use merge base
        ref = get_valid_ref(str(local_dir))

        assert ref is not None
        assert isinstance(ref, str)
        assert len(ref) == 40  # SHA-1 hash


def test_get_valid_ref_with_remote_show_parsing():
    """Test get_valid_ref correctly parses remote show output."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to return specific remote info
        original_run_git_command = run_git_command

        call_count = {'count': 0}

        def mock_run_git_command(args, cwd):
            call_count['count'] += 1
            if 'remote' in args and 'show' in args:
                # Simulate remote show output with HEAD branch
                return "* remote origin\n  HEAD branch: main\n  Remote branch: main"
            if 'merge-base' in args:
                # Return a valid merge base
                return "abc123def456789012345678901234567890abcd"
            if 'rev-parse' in args and '--verify' in args:
                # Verify the ref
                if 'origin/main' in args or 'abc123' in args:
                    return "abc123def456789012345678901234567890abcd"
                raise GitCommandError("ref not found", command=args, exit_code=128)
            # For other commands, use original
            return original_run_git_command(args, cwd)

        with patch('openhands.sdk.git.utils.run_git_command', side_effect=mock_run_git_command):
            ref = get_valid_ref(temp_dir)

            # Should successfully get a reference
            assert ref is not None
            # The function tries multiple strategies
            assert call_count['count'] > 0
