"""Tests for git_diff.py functionality using temporary directories and bash commands."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from openhands.sdk.git.git_diff import get_closest_git_repo, get_git_diff
from openhands.sdk.git.models import GitDiff


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


def run_in_directory(temp_dir: str, func, *args, **kwargs):
    """Helper to run a function in a specific directory."""
    original_cwd = os.getcwd()
    try:
        os.chdir(temp_dir)
        return func(*args, **kwargs)
    finally:
        os.chdir(original_cwd)


def test_get_git_diff_new_file():
    """Test get_git_diff with a new file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a new file
        test_file = Path(temp_dir) / "new_file.txt"
        test_content = "This is a new file\nwith multiple lines\nof content."
        test_file.write_text(test_content)

        diff = run_in_directory(temp_dir, get_git_diff, "new_file.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == test_content
        assert diff.original == ""  # Empty string for new files


def test_get_git_diff_modified_file():
    """Test get_git_diff with a modified file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial file
        test_file = Path(temp_dir) / "modified_file.txt"
        original_content = "Original content\nLine 2\nLine 3"
        test_file.write_text(original_content)

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify the file
        modified_content = "Modified content\nLine 2 changed\nLine 3\nNew line 4"
        test_file.write_text(modified_content)

        diff = run_in_directory(temp_dir, get_git_diff, "modified_file.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == modified_content
        # For repos without remote, original is empty when comparing against empty tree
        assert diff.original == ""


def test_get_git_diff_deleted_file():
    """Test get_git_diff with a deleted file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial file
        test_file = Path(temp_dir) / "deleted_file.txt"
        original_content = "This file will be deleted\nLine 2\nLine 3"
        test_file.write_text(original_content)

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Delete the file
        os.remove(test_file)

        # The function will raise GitPathError for deleted files
        from openhands.sdk.git.exceptions import GitPathError

        with pytest.raises(GitPathError):
            run_in_directory(temp_dir, get_git_diff, "deleted_file.txt")


def test_get_git_diff_nested_path():
    """Test get_git_diff with files in nested directories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create nested directory structure
        nested_dir = Path(temp_dir) / "src" / "utils"
        nested_dir.mkdir(parents=True)

        # Create and commit initial file
        test_file = nested_dir / "helper.py"
        original_content = "def helper():\n    return 'original'"
        test_file.write_text(original_content)

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify the file
        modified_content = (
            "def helper():\n    return 'modified'\n\ndef new_function():\n    pass"
        )
        test_file.write_text(modified_content)

        diff = run_in_directory(temp_dir, get_git_diff, "src/utils/helper.py")

        assert isinstance(diff, GitDiff)
        assert diff.modified == modified_content
        # For repos without remote, original is empty when comparing against empty tree
        assert diff.original == ""


def test_get_git_diff_no_repository():
    """Test get_git_diff with a non-git directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Don't initialize git repo
        test_file = Path(temp_dir) / "file.txt"
        test_file.write_text("Content")

        from openhands.sdk.git.exceptions import GitRepositoryError

        with pytest.raises(GitRepositoryError):
            run_in_directory(temp_dir, get_git_diff, "file.txt")


def test_get_git_diff_nonexistent_file():
    """Test get_git_diff with a nonexistent file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        from openhands.sdk.git.exceptions import GitPathError

        with pytest.raises(GitPathError):
            run_in_directory(temp_dir, get_git_diff, "nonexistent.txt")


def test_get_closest_git_repo():
    """Test the get_closest_git_repo helper function."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create nested directory structure
        nested_dir = Path(temp_dir) / "src" / "utils"
        nested_dir.mkdir(parents=True)

        # Test finding git repo from nested directory
        git_repo = get_closest_git_repo(nested_dir)
        # Compare resolved paths to avoid symlink differences on macOS
        # Example: /var is a symlink to /private/var
        assert git_repo is not None
        assert git_repo.resolve() == Path(temp_dir).resolve()

        # Test with non-git directory
        with tempfile.TemporaryDirectory() as non_git_dir:
            git_repo = get_closest_git_repo(Path(non_git_dir))
            assert git_repo is None


def test_git_diff_model_properties():
    """Test GitDiff model properties and serialization."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit initial file
        test_file = Path(temp_dir) / "model_test.py"
        original_content = "# Original model test"
        test_file.write_text(original_content)

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify the file
        modified_content = "# Modified model test\nprint('Hello')"
        test_file.write_text(modified_content)

        diff = run_in_directory(temp_dir, get_git_diff, "model_test.py")

        # Test model properties
        assert isinstance(diff, GitDiff)
        assert isinstance(diff.modified, str)
        assert isinstance(diff.original, str)
        assert diff.modified == modified_content
        # For repos without remote, original is empty when comparing against empty tree
        assert diff.original == ""

        # Test serialization
        diff_dict = diff.model_dump()
        assert "modified" in diff_dict
        assert "original" in diff_dict
        assert diff_dict["modified"] == modified_content
        assert diff_dict["original"] == ""


def test_git_diff_with_empty_file():
    """Test git diff with empty files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit empty file
        test_file = Path(temp_dir) / "empty.txt"
        test_file.write_text("")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Add content to the file
        new_content = "Now has content"
        test_file.write_text(new_content)

        diff = run_in_directory(temp_dir, get_git_diff, "empty.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == new_content
        assert diff.original == ""


def test_git_diff_with_special_characters():
    """Test git diff with files containing special characters."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create file with special characters
        test_file = Path(temp_dir) / "special_chars.txt"
        original_content = (
            "Original: àáâãäå\n中文\n🚀 emoji\n\"quotes\" and 'apostrophes'"
        )
        test_file.write_text(original_content, encoding="utf-8")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify with more special characters
        modified_content = (
            "Modified: àáâãäå\n中文修改\n🎉 new emoji\n"
            "\"new quotes\" and 'new apostrophes'\n\ttabs and\nlines"
        )
        test_file.write_text(modified_content, encoding="utf-8")

        diff = run_in_directory(temp_dir, get_git_diff, "special_chars.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == modified_content
        # For repos without remote, original is empty when comparing against empty tree
        assert diff.original == ""


def test_git_diff_large_file_error():
    """Test git diff with a file that's too large."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a file larger than MAX_FILE_SIZE_FOR_GIT_DIFF (1MB)
        test_file = Path(temp_dir) / "large_file.txt"
        large_content = "x" * (1024 * 1024 + 1)  # 1MB + 1 byte
        test_file.write_text(large_content)

        from openhands.sdk.git.exceptions import GitPathError

        with pytest.raises(GitPathError):
            run_in_directory(temp_dir, get_git_diff, "large_file.txt")
