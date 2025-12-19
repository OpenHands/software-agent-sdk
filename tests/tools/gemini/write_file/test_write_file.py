"""Tests for write_file tool."""

from openhands.tools.gemini.write_file.definition import WriteFileAction
from openhands.tools.gemini.write_file.impl import WriteFileExecutor


def test_write_file_create_new(tmp_path):
    """Test creating a new file."""
    executor = WriteFileExecutor(workspace_root=str(tmp_path))
    action = WriteFileAction(file_path="new.txt", content="hello world\n")
    obs = executor(action)

    assert not obs.is_error
    assert obs.is_new_file
    assert obs.file_path == str(tmp_path / "new.txt")
    assert obs.old_content is None
    assert obs.new_content == "hello world\n"

    # Verify file was created
    assert (tmp_path / "new.txt").exists()
    assert (tmp_path / "new.txt").read_text() == "hello world\n"


def test_write_file_overwrite_existing(tmp_path):
    """Test overwriting an existing file."""
    # Create existing file
    test_file = tmp_path / "existing.txt"
    test_file.write_text("old content\n")

    executor = WriteFileExecutor(workspace_root=str(tmp_path))
    action = WriteFileAction(file_path="existing.txt", content="new content\n")
    obs = executor(action)

    assert not obs.is_error
    assert not obs.is_new_file
    assert obs.old_content == "old content\n"
    assert obs.new_content == "new content\n"

    # Verify file was overwritten
    assert test_file.read_text() == "new content\n"


def test_write_file_create_directories(tmp_path):
    """Test creating parent directories."""
    executor = WriteFileExecutor(workspace_root=str(tmp_path))
    action = WriteFileAction(file_path="subdir/nested/file.txt", content="content\n")
    obs = executor(action)

    assert not obs.is_error
    assert obs.is_new_file

    # Verify directories and file were created
    assert (tmp_path / "subdir" / "nested" / "file.txt").exists()
    assert (tmp_path / "subdir" / "nested" / "file.txt").read_text() == "content\n"


def test_write_file_directory_error(tmp_path):
    """Test writing to a directory path returns error."""
    # Create a directory
    test_dir = tmp_path / "testdir"
    test_dir.mkdir()

    executor = WriteFileExecutor(workspace_root=str(tmp_path))
    action = WriteFileAction(file_path="testdir", content="content\n")
    obs = executor(action)

    assert obs.is_error
    assert "directory" in obs.text.lower()


def test_write_file_absolute_path(tmp_path):
    """Test writing with absolute path."""
    test_file = tmp_path / "test.txt"

    executor = WriteFileExecutor(workspace_root=str(tmp_path))
    action = WriteFileAction(file_path=str(test_file), content="content\n")
    obs = executor(action)

    assert not obs.is_error
    assert test_file.exists()
    assert test_file.read_text() == "content\n"


def test_write_file_empty_content(tmp_path):
    """Test writing empty content."""
    executor = WriteFileExecutor(workspace_root=str(tmp_path))
    action = WriteFileAction(file_path="empty.txt", content="")
    obs = executor(action)

    assert not obs.is_error
    assert obs.is_new_file
    assert (tmp_path / "empty.txt").exists()
    assert (tmp_path / "empty.txt").read_text() == ""
