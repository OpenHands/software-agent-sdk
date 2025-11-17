"""Tests for git_changes.py functionality using temporary directories and bash commands."""  # noqa: E501

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from openhands.sdk.git.git_changes import get_changes_in_repo, get_git_changes
from openhands.sdk.git.models import GitChange, GitChangeStatus


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


def test_get_changes_in_repo_empty_repository():
    """Test get_changes_in_repo with an empty repository."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        changes = get_changes_in_repo(temp_dir)
        assert changes == []


def test_get_changes_in_repo_new_files():
    """Test get_changes_in_repo with new files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create new files
        (Path(temp_dir) / "file1.txt").write_text("Hello World")
        (Path(temp_dir) / "file2.py").write_text("print('Hello')")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 2

        # Sort by path for consistent testing
        changes.sort(key=lambda x: str(x.path))

        assert changes[0].path == Path("file1.txt")
        assert changes[0].status == GitChangeStatus.ADDED

        assert changes[1].path == Path("file2.py")
        assert changes[1].status == GitChangeStatus.ADDED


def test_get_changes_in_repo_modified_files():
    """Test get_changes_in_repo with modified files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial files
        (Path(temp_dir) / "file1.txt").write_text("Initial content")
        (Path(temp_dir) / "file2.py").write_text("print('Initial')")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify files
        (Path(temp_dir) / "file1.txt").write_text("Modified content")
        (Path(temp_dir) / "file2.py").write_text("print('Modified')")

        changes = get_changes_in_repo(temp_dir)

        # The function compares against empty tree for new repos without remote
        # So modified files appear as ADDED since there's no remote origin
        assert len(changes) == 2

        # Sort by path for consistent testing
        changes.sort(key=lambda x: str(x.path))

        assert changes[0].path == Path("file1.txt")
        assert changes[0].status == GitChangeStatus.ADDED

        assert changes[1].path == Path("file2.py")
        assert changes[1].status == GitChangeStatus.ADDED


def test_get_changes_in_repo_deleted_files():
    """Test get_changes_in_repo with deleted files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial files
        (Path(temp_dir) / "file1.txt").write_text("Content to delete")
        (Path(temp_dir) / "file2.py").write_text("print('To delete')")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Delete files
        os.remove(Path(temp_dir) / "file1.txt")
        os.remove(Path(temp_dir) / "file2.py")

        changes = get_changes_in_repo(temp_dir)

        # For repos without remote, deleted files don't show up in diff against empty tree  # noqa: E501
        # This is expected behavior - the function compares against empty tree
        assert len(changes) == 0


def test_get_changes_in_repo_mixed_changes():
    """Test get_changes_in_repo with mixed file changes."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial files
        (Path(temp_dir) / "existing.txt").write_text("Existing content")
        (Path(temp_dir) / "to_modify.py").write_text("print('Original')")
        (Path(temp_dir) / "to_delete.md").write_text("# To Delete")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Make mixed changes
        (Path(temp_dir) / "new_file.txt").write_text("New file content")  # Added
        (Path(temp_dir) / "to_modify.py").write_text("print('Modified')")  # Modified
        os.remove(Path(temp_dir) / "to_delete.md")  # Deleted

        changes = get_changes_in_repo(temp_dir)

        # For repos without remote, all files (existing, new, modified) show up as ADDED
        # when comparing against empty tree. Deleted files don't appear.
        assert len(changes) == 3

        # Convert to dict for easier testing
        changes_dict = {str(change.path): change.status for change in changes}

        assert changes_dict["existing.txt"] == GitChangeStatus.ADDED
        assert changes_dict["new_file.txt"] == GitChangeStatus.ADDED
        assert changes_dict["to_modify.py"] == GitChangeStatus.ADDED


def test_get_changes_in_repo_nested_directories():
    """Test get_changes_in_repo with files in nested directories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create nested directory structure
        nested_dir = Path(temp_dir) / "src" / "utils"
        nested_dir.mkdir(parents=True)

        (nested_dir / "helper.py").write_text("def helper(): pass")
        (Path(temp_dir) / "src" / "main.py").write_text("import utils.helper")
        (Path(temp_dir) / "README.md").write_text("# Project")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 3

        # Convert to set of paths for easier testing
        paths = {str(change.path) for change in changes}

        assert "src/utils/helper.py" in paths
        assert "src/main.py" in paths
        assert "README.md" in paths

        # All should be added files
        for change in changes:
            assert change.status == GitChangeStatus.ADDED


def test_get_changes_in_repo_staged_and_unstaged():
    """Test get_changes_in_repo with both staged and unstaged changes."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial file
        (Path(temp_dir) / "file.txt").write_text("Initial")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Make changes and stage some
        (Path(temp_dir) / "file.txt").write_text("Modified")
        (Path(temp_dir) / "staged.txt").write_text("Staged content")
        (Path(temp_dir) / "unstaged.txt").write_text("Unstaged content")

        # Stage some changes
        run_bash_command("git add staged.txt", temp_dir)

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 3

        # Convert to dict for easier testing
        changes_dict = {str(change.path): change.status for change in changes}

        # All files appear as ADDED when comparing against empty tree
        assert changes_dict["file.txt"] == GitChangeStatus.ADDED
        assert changes_dict["staged.txt"] == GitChangeStatus.ADDED
        assert changes_dict["unstaged.txt"] == GitChangeStatus.ADDED


def test_get_changes_in_repo_non_git_directory():
    """Test get_changes_in_repo with a non-git directory."""
    from openhands.sdk.git.exceptions import GitRepositoryError

    with tempfile.TemporaryDirectory() as temp_dir:
        # Don't initialize git repo
        (Path(temp_dir) / "file.txt").write_text("Content")

        with pytest.raises(GitRepositoryError):
            get_changes_in_repo(temp_dir)


def test_get_changes_in_repo_nonexistent_directory():
    """Test get_changes_in_repo with a nonexistent directory."""
    from openhands.sdk.git.exceptions import GitRepositoryError

    # The function will raise an exception for nonexistent directories
    with pytest.raises(GitRepositoryError):
        get_changes_in_repo("/nonexistent/directory")


def test_get_git_changes_function():
    """Test the get_git_changes function (main entry point)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create test files
        (Path(temp_dir) / "test1.txt").write_text("Test content 1")
        (Path(temp_dir) / "test2.py").write_text("print('Test 2')")

        # Call get_git_changes with explicit path
        changes = get_git_changes(temp_dir)

        assert len(changes) == 2

        # Sort by path for consistent testing
        changes.sort(key=lambda x: str(x.path))

        assert changes[0].path == Path("test1.txt")
        assert changes[0].status == GitChangeStatus.ADDED

        assert changes[1].path == Path("test2.py")
        assert changes[1].status == GitChangeStatus.ADDED


def test_get_git_changes_with_path_argument():
    """Test get_git_changes with explicit path argument."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create test files
        (Path(temp_dir) / "explicit_path.txt").write_text("Explicit path test")

        changes = get_git_changes(temp_dir)

        assert len(changes) == 1
        assert changes[0].path == Path("explicit_path.txt")
        assert changes[0].status == GitChangeStatus.ADDED


def test_git_change_model_properties():
    """Test GitChange model properties and serialization."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a test file
        test_file = Path(temp_dir) / "model_test.py"
        test_file.write_text("# Model test file")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 1
        change = changes[0]

        # Test model properties
        assert isinstance(change, GitChange)
        assert isinstance(change.path, Path)
        assert isinstance(change.status, GitChangeStatus)
        assert change.path == Path("model_test.py")
        assert change.status == GitChangeStatus.ADDED

        # Test serialization
        change_dict = change.model_dump()
        assert "path" in change_dict
        assert "status" in change_dict
        assert change_dict["status"] == GitChangeStatus.ADDED


def test_git_changes_with_gitignore():
    """Test that gitignore files are respected."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create .gitignore
        (Path(temp_dir) / ".gitignore").write_text("*.log\n__pycache__/\n")

        # Create files that should be ignored
        (Path(temp_dir) / "debug.log").write_text("Log content")
        pycache_dir = Path(temp_dir) / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "module.pyc").write_text("Compiled python")

        # Create files that should not be ignored
        (Path(temp_dir) / "main.py").write_text("print('Main')")

        changes = get_changes_in_repo(temp_dir)

        # Should only see .gitignore and main.py, not the ignored files
        paths = {str(change.path) for change in changes}

        assert ".gitignore" in paths
        assert "main.py" in paths
        assert "debug.log" not in paths
        assert "__pycache__/module.pyc" not in paths


def test_git_changes_with_binary_files():
    """Test git changes detection with binary files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a binary file (simulate with bytes)
        binary_file = Path(temp_dir) / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

        # Create a text file
        (Path(temp_dir) / "text.txt").write_text("Text content")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 2

        # Both files should be detected as added
        paths = {str(change.path) for change in changes}
        assert "image.png" in paths
        assert "text.txt" in paths

        for change in changes:
            assert change.status == GitChangeStatus.ADDED


def test_map_git_status_to_enum():
    """Test _map_git_status_to_enum helper function."""
    from openhands.sdk.git.git_changes import _map_git_status_to_enum

    assert _map_git_status_to_enum("M") == GitChangeStatus.UPDATED
    assert _map_git_status_to_enum("A") == GitChangeStatus.ADDED
    assert _map_git_status_to_enum("D") == GitChangeStatus.DELETED
    assert _map_git_status_to_enum("U") == GitChangeStatus.UPDATED


def test_map_git_status_to_enum_invalid():
    """Test _map_git_status_to_enum with invalid status."""
    from openhands.sdk.git.git_changes import _map_git_status_to_enum

    with pytest.raises(ValueError):
        _map_git_status_to_enum("X")

    with pytest.raises(ValueError):
        _map_git_status_to_enum("INVALID")


def test_get_changes_with_rename_operation():
    """Test get_changes_in_repo with renamed files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit a file
        (Path(temp_dir) / "old_name.txt").write_text("Content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Rename the file using git mv
        run_bash_command("git mv old_name.txt new_name.txt", temp_dir)

        # Get changes (comparing against empty tree will show new file as added)
        changes = get_changes_in_repo(temp_dir)

        # For repos without remote, we only see the new file as added
        assert len(changes) >= 1

        paths = {str(change.path) for change in changes}
        assert "new_name.txt" in paths


def test_get_changes_with_copy_like_operation():
    """Test get_changes_in_repo with copy-like operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit a file
        original_content = "Original content that will be copied"
        (Path(temp_dir) / "original.txt").write_text(original_content)
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Copy the file manually (not using git)
        (Path(temp_dir) / "copy.txt").write_text(original_content)

        changes = get_changes_in_repo(temp_dir)

        # Should see both files as added when comparing against empty tree
        assert len(changes) >= 1

        paths = {str(change.path) for change in changes}
        assert "original.txt" in paths or "copy.txt" in paths


def test_get_git_changes_with_nested_git_repos():
    """Test get_git_changes with nested git repositories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a file in the main repo
        (Path(temp_dir) / "main_repo_file.txt").write_text("Main repo content")

        # Create a nested git repository
        nested_dir = Path(temp_dir) / "nested_repo"
        nested_dir.mkdir()
        setup_git_repo(str(nested_dir))
        (nested_dir / "nested_file.txt").write_text("Nested repo content")

        # Get changes for the main repo
        changes = get_git_changes(temp_dir)

        # Should see files from both repos
        paths = {str(change.path) for change in changes}

        # Main repo file
        assert "main_repo_file.txt" in paths

        # Nested repo file with proper path
        assert "nested_repo/nested_file.txt" in paths or "nested_file.txt" in paths


def test_get_changes_empty_git_diff_output():
    """Test handling of empty lines in git diff output."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Empty repo should produce no changes
        changes = get_changes_in_repo(temp_dir)

        assert changes == []


def test_get_changes_with_untracked_files_only():
    """Test get_changes_in_repo with only untracked files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create untracked files
        (Path(temp_dir) / "untracked1.txt").write_text("Untracked 1")
        (Path(temp_dir) / "untracked2.py").write_text("# Untracked 2")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 2

        for change in changes:
            assert change.status == GitChangeStatus.ADDED


def test_get_changes_respects_exclude_standard():
    """Test that git changes respect .gitignore patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create .gitignore
        (Path(temp_dir) / ".gitignore").write_text("*.ignore\ntemp/\n")

        # Create files
        (Path(temp_dir) / "tracked.txt").write_text("Tracked")
        (Path(temp_dir) / "file.ignore").write_text("Should be ignored")

        # Create temp directory with file
        temp_subdir = Path(temp_dir) / "temp"
        temp_subdir.mkdir()
        (temp_subdir / "ignored.txt").write_text("In ignored dir")

        changes = get_changes_in_repo(temp_dir)

        paths = {str(change.path) for change in changes}

        # Should see .gitignore and tracked.txt
        assert ".gitignore" in paths
        assert "tracked.txt" in paths

        # Should not see ignored files
        assert "file.ignore" not in paths
        assert "temp/ignored.txt" not in paths


def test_get_changes_with_subdirectories():
    """Test get_changes_in_repo with files in multiple subdirectories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create complex directory structure
        (Path(temp_dir) / "root.txt").write_text("Root level")

        src_dir = Path(temp_dir) / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("Main file")

        utils_dir = src_dir / "utils"
        utils_dir.mkdir()
        (utils_dir / "helper.py").write_text("Helper file")

        tests_dir = Path(temp_dir) / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("Test file")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 4

        paths = {str(change.path) for change in changes}
        assert "root.txt" in paths
        assert "src/main.py" in paths
        assert "src/utils/helper.py" in paths
        assert "tests/test_main.py" in paths


def test_get_changes_sorted_output():
    """Test that get_git_changes returns sorted results."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create files in non-alphabetical order
        (Path(temp_dir) / "zebra.txt").write_text("Z")
        (Path(temp_dir) / "apple.txt").write_text("A")
        (Path(temp_dir) / "banana.txt").write_text("B")

        changes = get_git_changes(temp_dir)

        # Check that changes are sorted by path
        paths = [str(change.path) for change in changes]
        assert paths == sorted(paths)


def test_get_changes_with_staged_files():
    """Test that staged files are detected in changes."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and stage files
        (Path(temp_dir) / "staged.txt").write_text("Staged content")
        run_bash_command("git add staged.txt", temp_dir)

        # Create unstaged file
        (Path(temp_dir) / "unstaged.txt").write_text("Unstaged content")

        changes = get_changes_in_repo(temp_dir)

        # Both should be detected
        paths = {str(change.path) for change in changes}
        assert "staged.txt" in paths
        assert "unstaged.txt" in paths


def test_get_changes_path_object_types():
    """Test that all changes return Path objects."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        (Path(temp_dir) / "test.txt").write_text("Test")

        changes = get_changes_in_repo(temp_dir)

        assert len(changes) == 1
        assert isinstance(changes[0].path, Path)
        assert isinstance(changes[0].status, GitChangeStatus)


def test_get_changes_with_renamed_files():
    """Test get_changes_in_repo with renamed files (R status)."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial file
        (Path(temp_dir) / "old_name.txt").write_text("Content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Mock run_git_command to return rename output
        original_run_git_command = __import__(
            'openhands.sdk.git.utils', fromlist=['run_git_command']
        ).run_git_command

        def mock_run_git_command(args, cwd):
            if 'diff' in args and '--name-status' in args:
                # Return rename status with similarity percentage
                return "R100\told_name.txt\tnew_name.txt"
            if 'ls-files' in args:
                return ""
            # For other commands, use original
            return original_run_git_command(args, cwd)

        with patch(
            'openhands.sdk.git.git_changes.run_git_command', side_effect=mock_run_git_command
        ):
            changes = get_changes_in_repo(temp_dir)

            # Rename should be represented as delete old + add new
            assert len(changes) == 2

            paths_and_statuses = {(str(c.path), c.status) for c in changes}
            assert ("old_name.txt", GitChangeStatus.DELETED) in paths_and_statuses
            assert ("new_name.txt", GitChangeStatus.ADDED) in paths_and_statuses


def test_get_changes_with_copied_files():
    """Test get_changes_in_repo with copied files (C status)."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial file
        (Path(temp_dir) / "original.txt").write_text("Content to copy")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Mock run_git_command to return copy output
        original_run_git_command = __import__(
            'openhands.sdk.git.utils', fromlist=['run_git_command']
        ).run_git_command

        def mock_run_git_command(args, cwd):
            if 'diff' in args and '--name-status' in args:
                # Return copy status with similarity percentage
                return "C100\toriginal.txt\tcopied.txt"
            if 'ls-files' in args:
                return ""
            # For other commands, use original
            return original_run_git_command(args, cwd)

        with patch(
            'openhands.sdk.git.git_changes.run_git_command', side_effect=mock_run_git_command
        ):
            changes = get_changes_in_repo(temp_dir)

            # Copy should be represented as add new (original remains)
            assert len(changes) == 1
            assert changes[0].path == Path("copied.txt")
            assert changes[0].status == GitChangeStatus.ADDED


def test_get_changes_with_untracked_files_error():
    """Test get_changes_in_repo when untracked files command fails."""
    from unittest.mock import patch
    from openhands.sdk.git.exceptions import GitCommandError

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a committed file and a modified file
        (Path(temp_dir) / "committed.txt").write_text("Committed")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        (Path(temp_dir) / "modified.txt").write_text("Modified")
        run_bash_command("git add modified.txt", temp_dir)

        # Mock the run_git_command to fail on ls-files command
        original_run_git_command = __import__(
            'openhands.sdk.git.utils', fromlist=['run_git_command']
        ).run_git_command

        def mock_run_git_command(args, cwd):
            if 'ls-files' in args:
                raise GitCommandError(
                    "ls-files failed", command=args, exit_code=1, stderr="Mock error"
                )
            return original_run_git_command(args, cwd)

        with patch(
            'openhands.sdk.git.git_changes.run_git_command', side_effect=mock_run_git_command
        ):
            # Should still work, just without untracked files
            changes = get_changes_in_repo(temp_dir)

            # Should have at least the modified file
            assert len(changes) >= 1


def test_get_changes_with_no_valid_ref():
    """Test get_changes_in_repo when no valid git reference is found."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock get_valid_ref to return None
        with patch('openhands.sdk.git.git_changes.get_valid_ref', return_value=None):
            changes = get_changes_in_repo(temp_dir)

            # Should return empty list
            assert changes == []


def test_get_changes_with_git_diff_failure():
    """Test get_changes_in_repo when git diff command fails."""
    from unittest.mock import patch
    from openhands.sdk.git.exceptions import GitCommandError

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to fail on diff command
        def mock_run_git_command(args, cwd):
            if 'diff' in args:
                raise GitCommandError(
                    "diff failed", command=args, exit_code=1, stderr="Mock error"
                )
            # Return valid ref for other commands
            return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        with patch(
            'openhands.sdk.git.git_changes.run_git_command',
            side_effect=mock_run_git_command,
        ):
            with pytest.raises(GitCommandError) as exc_info:
                get_changes_in_repo(temp_dir)

            assert "diff failed" in str(exc_info.value)


def test_get_changes_with_empty_lines_in_output():
    """Test get_changes_in_repo handles empty lines in git diff output."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to return output with empty lines
        def mock_run_git_command(args, cwd):
            if 'diff' in args and '--name-status' in args:
                # Return output with empty lines
                return "A\tfile1.txt\n\n\nM\tfile2.txt\n\n"
            if 'ls-files' in args:
                return ""
            # For other commands (like getting ref)
            return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        with patch(
            'openhands.sdk.git.git_changes.run_git_command',
            side_effect=mock_run_git_command,
        ):
            changes = get_changes_in_repo(temp_dir)

            # Should skip empty lines and process valid ones
            assert len(changes) == 2
            paths = {str(c.path) for c in changes}
            assert "file1.txt" in paths
            assert "file2.txt" in paths


def test_get_changes_with_unexpected_line_format():
    """Test get_changes_in_repo with unexpected git diff line format."""
    from unittest.mock import patch
    from openhands.sdk.git.exceptions import GitCommandError

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to return malformed output
        def mock_run_git_command(args, cwd):
            if 'diff' in args and '--name-status' in args:
                # Return output with only one part (invalid format)
                return "InvalidStatusWithoutFile"
            if 'ls-files' in args:
                return ""
            return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        with patch(
            'openhands.sdk.git.git_changes.run_git_command',
            side_effect=mock_run_git_command,
        ):
            with pytest.raises(GitCommandError) as exc_info:
                get_changes_in_repo(temp_dir)

            assert "Unexpected git diff output format" in str(exc_info.value)


def test_get_changes_with_unexpected_three_part_format():
    """Test get_changes_in_repo with unexpected three-part format."""
    from unittest.mock import patch
    from openhands.sdk.git.exceptions import GitCommandError

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to return unexpected three-part output
        def mock_run_git_command(args, cwd):
            if 'diff' in args and '--name-status' in args:
                # Return three parts but not R or C
                return "X\tpart1\tpart2\tpart3"
            if 'ls-files' in args:
                return ""
            return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        with patch(
            'openhands.sdk.git.git_changes.run_git_command',
            side_effect=mock_run_git_command,
        ):
            with pytest.raises(GitCommandError) as exc_info:
                get_changes_in_repo(temp_dir)

            assert "Unexpected git diff output format" in str(exc_info.value)


def test_get_changes_with_unknown_status_code():
    """Test get_changes_in_repo with unknown status code."""
    from unittest.mock import patch
    from openhands.sdk.git.exceptions import GitCommandError

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Mock run_git_command to return unknown status
        def mock_run_git_command(args, cwd):
            if 'diff' in args and '--name-status' in args:
                # Return unknown status code
                return "X\tfile.txt"
            if 'ls-files' in args:
                return ""
            return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        with patch(
            'openhands.sdk.git.git_changes.run_git_command',
            side_effect=mock_run_git_command,
        ):
            with pytest.raises(GitCommandError) as exc_info:
                get_changes_in_repo(temp_dir)

            assert "Unexpected git status" in str(exc_info.value)
