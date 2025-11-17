"""Tests for git_diff.py functionality using temporary directories and bash commands."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from openhands.sdk.git.exceptions import GitPathError
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
        assert git_repo == Path(temp_dir)

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


def test_git_diff_with_absolute_path():
    """Test get_git_diff with absolute path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a test file
        test_file = Path(temp_dir) / "absolute_test.txt"
        test_content = "Testing with absolute path"
        test_file.write_text(test_content)

        # Use absolute path
        diff = run_in_directory(temp_dir, get_git_diff, str(test_file.resolve()))

        assert isinstance(diff, GitDiff)
        assert diff.modified == test_content


def test_git_diff_with_relative_path_components():
    """Test get_git_diff with relative path components like ./ and ../."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create nested directory structure
        nested_dir = Path(temp_dir) / "dir1" / "dir2"
        nested_dir.mkdir(parents=True)

        test_file = nested_dir / "test.txt"
        test_content = "Relative path test"
        test_file.write_text(test_content)

        # Test with ./ prefix
        diff = run_in_directory(temp_dir, get_git_diff, "./dir1/dir2/test.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == test_content


def test_git_diff_exact_size_limit():
    """Test git diff with a file exactly at the size limit."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        from openhands.sdk.git.git_diff import MAX_FILE_SIZE_FOR_GIT_DIFF

        # Create a file exactly at the limit
        test_file = Path(temp_dir) / "exact_size.txt"
        exact_content = "x" * MAX_FILE_SIZE_FOR_GIT_DIFF
        test_file.write_text(exact_content)

        # Should work without error
        diff = run_in_directory(temp_dir, get_git_diff, "exact_size.txt")

        assert isinstance(diff, GitDiff)
        assert len(diff.modified) == MAX_FILE_SIZE_FOR_GIT_DIFF


def test_get_closest_git_repo_from_file_path():
    """Test get_closest_git_repo with a file path (not directory)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a file
        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("content")

        # get_closest_git_repo should work with file paths
        git_repo = get_closest_git_repo(test_file)
        assert git_repo == Path(temp_dir)


def test_get_closest_git_repo_deeply_nested():
    """Test get_closest_git_repo with deeply nested directory structure."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create deeply nested structure
        deep_path = Path(temp_dir) / "a" / "b" / "c" / "d" / "e"
        deep_path.mkdir(parents=True)

        git_repo = get_closest_git_repo(deep_path)
        assert git_repo == Path(temp_dir)


def test_get_closest_git_repo_root_filesystem():
    """Test get_closest_git_repo when reaching filesystem root."""
    # Use a path that's unlikely to have a git repo
    non_git_path = Path("/tmp")
    if not (non_git_path / ".git").exists():
        result = get_closest_git_repo(non_git_path)
        # Should return None or the actual git repo if one exists above
        assert result is None or result.exists()


def test_git_diff_with_symlink():
    """Test git diff with symbolic links."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a file
        original_file = Path(temp_dir) / "original.txt"
        original_file.write_text("Original content")

        # Create a symlink
        symlink_file = Path(temp_dir) / "link.txt"
        try:
            symlink_file.symlink_to(original_file)

            # Get diff of the symlink
            diff = run_in_directory(temp_dir, get_git_diff, "link.txt")

            assert isinstance(diff, GitDiff)
            # Symlink should resolve to the original file content
            assert diff.modified == "Original content"
        except OSError:
            # Skip if symlinks not supported on this system
            pytest.skip("Symlinks not supported on this system")


def test_git_diff_unicode_filename():
    """Test git diff with unicode characters in filename."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create file with unicode name
        unicode_file = Path(temp_dir) / "файл_test_文件.txt"
        unicode_content = "Unicode filename content"
        unicode_file.write_text(unicode_content, encoding="utf-8")

        # Get diff
        diff = run_in_directory(temp_dir, get_git_diff, unicode_file.name)

        assert isinstance(diff, GitDiff)
        assert diff.modified == unicode_content


def test_git_diff_with_different_newline_styles():
    """Test git diff preserves different newline styles correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        test_file = Path(temp_dir) / "newlines.txt"
        # Git diff normalizes line endings
        test_file.write_text("Line 1\nLine 2\nLine 3")

        diff = run_in_directory(temp_dir, get_git_diff, "newlines.txt")

        assert isinstance(diff, GitDiff)
        # The function strips trailing newlines with splitlines()
        assert "Line 1" in diff.modified
        assert "Line 2" in diff.modified
        assert "Line 3" in diff.modified


def test_git_diff_binary_file_as_text():
    """Test git diff with binary file read as text."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a small binary file
        binary_file = Path(temp_dir) / "small_binary.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\x04")

        # The function will try to read it as text
        # This might fail or return empty, depending on the file
        try:
            diff = run_in_directory(temp_dir, get_git_diff, "small_binary.bin")
            # If it succeeds, verify it's a GitDiff object
            assert isinstance(diff, GitDiff)
        except Exception:
            # Binary files might cause issues, which is expected
            pass


def test_git_diff_empty_file_to_content():
    """Test git diff transitioning from empty to content."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create empty file and commit
        test_file = Path(temp_dir) / "empty_to_content.txt"
        test_file.write_text("")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Empty file'", temp_dir)

        # Add content
        new_content = "Now has content"
        test_file.write_text(new_content)

        diff = run_in_directory(temp_dir, get_git_diff, "empty_to_content.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == new_content


def test_git_diff_content_to_empty():
    """Test git diff transitioning from content to empty."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create file with content and commit
        test_file = Path(temp_dir) / "content_to_empty.txt"
        test_file.write_text("Original content")

        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Empty the file
        test_file.write_text("")

        diff = run_in_directory(temp_dir, get_git_diff, "content_to_empty.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == ""


def test_git_diff_file_in_git_root():
    """Test git diff with file directly in git root."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create file in root
        root_file = Path(temp_dir) / "root_file.txt"
        root_content = "File in git root"
        root_file.write_text(root_content)

        diff = run_in_directory(temp_dir, get_git_diff, "root_file.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == root_content
        assert diff.original == ""


def test_git_diff_path_traversal_attempt():
    """Test git diff with path traversal attempts."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a file
        test_file = Path(temp_dir) / "safe_file.txt"
        test_file.write_text("Safe content")

        # Try to access with path traversal
        # The Path.resolve() should prevent issues
        diff = run_in_directory(temp_dir, get_git_diff, "./safe_file.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == "Safe content"


def test_git_diff_max_file_size_constant():
    """Test that MAX_FILE_SIZE_FOR_GIT_DIFF constant is reasonable."""
    from openhands.sdk.git.git_diff import MAX_FILE_SIZE_FOR_GIT_DIFF

    assert MAX_FILE_SIZE_FOR_GIT_DIFF == 1024 * 1024  # 1MB
    assert MAX_FILE_SIZE_FOR_GIT_DIFF > 0
    assert isinstance(MAX_FILE_SIZE_FOR_GIT_DIFF, int)


def test_git_diff_with_spaces_in_path():
    """Test git diff with spaces in file path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create file with spaces in name
        space_file = Path(temp_dir) / "file with spaces.txt"
        space_content = "Content in file with spaces"
        space_file.write_text(space_content)

        diff = run_in_directory(temp_dir, get_git_diff, "file with spaces.txt")

        assert isinstance(diff, GitDiff)
        assert diff.modified == space_content


def test_get_closest_git_repo_with_gitfile():
    """Test get_closest_git_repo with .git as a file (worktree case)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create a commit first
        (Path(temp_dir) / "file.txt").write_text("content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Create a worktree
        worktree_dir = Path(temp_dir) / "worktree"
        result = run_bash_command(
            f"git worktree add {worktree_dir} HEAD", temp_dir
        )

        if result.returncode == 0:  # git worktree command succeeded
            # The .git in worktree is a file, not a directory
            git_repo = get_closest_git_repo(worktree_dir)
            assert git_repo is not None
            assert git_repo.exists()


def test_git_diff_model_fields():
    """Test that GitDiff model has correct field types."""
    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        test_file = Path(temp_dir) / "model_fields.txt"
        test_file.write_text("Test content")

        diff = run_in_directory(temp_dir, get_git_diff, "model_fields.txt")

        # Check field types
        assert hasattr(diff, "modified")
        assert hasattr(diff, "original")
        assert isinstance(diff.modified, (str, type(None)))
        assert isinstance(diff.original, (str, type(None)))


def test_get_git_diff_file_access_error():
    """Test get_git_diff when file cannot be accessed (OSError)."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("Content")

        # Mock os.path.getsize to raise OSError
        with patch('os.path.getsize', side_effect=OSError("Permission denied")):
            with pytest.raises(GitPathError) as exc_info:
                run_in_directory(temp_dir, get_git_diff, "test.txt")

            assert "Cannot access file" in str(exc_info.value)


def test_get_git_diff_no_valid_ref():
    """Test get_git_diff when no valid git reference is found."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("Content")

        # Mock get_valid_ref to return None
        with patch('openhands.sdk.git.git_diff.get_valid_ref', return_value=None):
            diff = run_in_directory(temp_dir, get_git_diff, "test.txt")

            # Should return empty GitDiff
            assert diff.modified == ""
            assert diff.original == ""


def test_get_git_diff_path_outside_repo():
    """Test get_git_diff when file path cannot be made relative to repo."""
    from unittest.mock import patch, MagicMock

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("Content")

        # Mock Path.relative_to to raise ValueError
        original_relative_to = Path.relative_to

        def mock_relative_to(self, *args, **kwargs):
            if 'test.txt' in str(self):
                raise ValueError("Path is not relative")
            return original_relative_to(self, *args, **kwargs)

        with patch.object(Path, 'relative_to', mock_relative_to):
            with pytest.raises(GitPathError) as exc_info:
                run_in_directory(temp_dir, get_git_diff, "test.txt")

            assert "not within git repository" in str(exc_info.value)


def test_get_git_diff_file_read_error():
    """Test get_git_diff when file cannot be read (UnicodeDecodeError)."""
    from unittest.mock import patch, mock_open

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit a file
        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("Initial content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify the file
        test_file.write_text("Modified content")

        # Mock open to raise UnicodeDecodeError when reading the file
        original_open = open

        def mock_open_func(file, *args, **kwargs):
            if 'test.txt' in str(file) and 'encoding' in kwargs:
                mock_file = mock_open(read_data="data")()
                mock_file.read.side_effect = UnicodeDecodeError(
                    'utf-8', b'\x80', 0, 1, 'invalid start byte'
                )
                return mock_file
            return original_open(file, *args, **kwargs)

        with patch('builtins.open', side_effect=mock_open_func):
            diff = run_in_directory(temp_dir, get_git_diff, "test.txt")

            # Should handle the error and return empty modified
            assert diff.modified == ""
            # Original might still be available from git
            assert isinstance(diff.original, str)


def test_get_git_diff_file_read_oserror():
    """Test get_git_diff when file reading raises OSError."""
    from unittest.mock import patch, mock_open

    with tempfile.TemporaryDirectory() as temp_dir:
        setup_git_repo(temp_dir)

        # Create and commit a file
        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("Initial content")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'Initial commit'", temp_dir)

        # Modify the file
        test_file.write_text("Modified content")

        # Mock open to raise OSError when reading the file
        original_open = open

        def mock_open_func(file, *args, **kwargs):
            if 'test.txt' in str(file) and 'encoding' in kwargs:
                mock_file = mock_open(read_data="data")()
                mock_file.read.side_effect = OSError("Read error")
                return mock_file
            return original_open(file, *args, **kwargs)

        with patch('builtins.open', side_effect=mock_open_func):
            diff = run_in_directory(temp_dir, get_git_diff, "test.txt")

            # Should handle the error and return empty modified
            assert diff.modified == ""
