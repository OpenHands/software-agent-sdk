"""Tests for list_directory tool."""

from openhands.tools.gemini.list_directory.definition import ListDirectoryAction
from openhands.tools.gemini.list_directory.impl import ListDirectoryExecutor


def test_list_directory_basic(tmp_path):
    """Test listing directory contents."""
    # Create some files and directories
    (tmp_path / "file1.txt").write_text("content")
    (tmp_path / "file2.py").write_text("code")
    (tmp_path / "subdir").mkdir()

    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path=".")
    obs = executor(action)

    assert not obs.is_error
    assert obs.total_count == 3
    assert not obs.is_truncated

    # Check entries
    names = [e.name for e in obs.entries]
    assert "file1.txt" in names
    assert "file2.py" in names
    assert "subdir" in names

    # Check that subdir is marked as directory
    subdir_entry = next(e for e in obs.entries if e.name == "subdir")
    assert subdir_entry.is_directory


def test_list_directory_empty(tmp_path):
    """Test listing empty directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path="empty")
    obs = executor(action)

    assert not obs.is_error
    assert obs.total_count == 0
    assert len(obs.entries) == 0


def test_list_directory_recursive(tmp_path):
    """Test recursive directory listing."""
    # Create nested structure
    (tmp_path / "file1.txt").write_text("content")
    (tmp_path / "subdir1").mkdir()
    (tmp_path / "subdir1" / "file2.txt").write_text("content")
    (tmp_path / "subdir1" / "subdir2").mkdir()
    (tmp_path / "subdir1" / "subdir2" / "file3.txt").write_text("content")

    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path=".", recursive=True)
    obs = executor(action)

    assert not obs.is_error
    # Should include files and directories up to 2 levels deep
    # Level 0: . (tmp_path)
    # Level 1: file1.txt, subdir1
    # Level 2: file2.txt (in subdir1), subdir2 (in subdir1)
    # file3.txt is at level 3 (in subdir2) so it won't be included
    names = [e.name for e in obs.entries]
    assert "file1.txt" in names
    assert "subdir1" in names
    assert "file2.txt" in names
    assert "subdir2" in names
    # file3.txt is at level 3, which is beyond our 2-level limit
    assert "file3.txt" not in names


def test_list_directory_not_found(tmp_path):
    """Test listing non-existent directory."""
    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path="nonexistent")
    obs = executor(action)

    assert obs.is_error
    assert "not found" in obs.text.lower()


def test_list_directory_not_a_directory(tmp_path):
    """Test listing a file instead of directory."""
    test_file = tmp_path / "file.txt"
    test_file.write_text("content")

    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path="file.txt")
    obs = executor(action)

    assert obs.is_error
    assert "not a directory" in obs.text.lower()


def test_list_directory_file_metadata(tmp_path):
    """Test that file metadata is included."""
    # Create a file
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path=".")
    obs = executor(action)

    assert not obs.is_error
    assert len(obs.entries) == 1

    entry = obs.entries[0]
    assert entry.name == "test.txt"
    assert not entry.is_directory
    assert entry.size == 11
    assert entry.modified_time is not None


def test_list_directory_absolute_path(tmp_path):
    """Test listing with absolute path."""
    (tmp_path / "file.txt").write_text("content")

    executor = ListDirectoryExecutor(workspace_root=str(tmp_path))
    action = ListDirectoryAction(dir_path=str(tmp_path))
    obs = executor(action)

    assert not obs.is_error
    assert obs.total_count == 1
    assert obs.entries[0].name == "file.txt"
