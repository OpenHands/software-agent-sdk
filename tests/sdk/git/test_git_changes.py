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
