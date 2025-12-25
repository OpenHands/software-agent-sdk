"""Tests for read_file tool."""

from openhands.tools.gemini.read_file.definition import ReadFileAction
from openhands.tools.gemini.read_file.impl import ReadFileExecutor


def test_read_file_basic(tmp_path):
    """Test reading a basic file."""
    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3\n")

    # Execute read_file
    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path="test.txt")
    obs = executor(action)

    assert not obs.is_error
    assert obs.file_path == str(test_file)
    assert "line 1" in obs.file_content
    assert "line 2" in obs.file_content
    assert "line 3" in obs.file_content
    assert not obs.is_truncated


def test_read_file_with_offset(tmp_path):
    """Test reading file with offset."""
    # Create a test file with many lines
    test_file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 21)]
    test_file.write_text("".join(lines))

    # Read with offset
    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path="test.txt", offset=10, limit=5)
    obs = executor(action)

    assert not obs.is_error
    assert "line 11" in obs.file_content
    assert "line 15" in obs.file_content
    assert "line 10" not in obs.file_content
    assert "line 16" not in obs.file_content


def test_read_file_truncation(tmp_path):
    """Test that large files are truncated."""
    # Create a large file
    test_file = tmp_path / "large.txt"
    lines = [f"line {i}\n" for i in range(1, 2000)]
    test_file.write_text("".join(lines))

    # Read without limit (should apply default MAX_LINES_PER_READ)
    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path="large.txt")
    obs = executor(action)

    assert not obs.is_error
    assert obs.is_truncated
    assert obs.total_lines == 1999
    assert obs.lines_shown is not None


def test_read_file_not_found(tmp_path):
    """Test reading non-existent file."""
    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path="nonexistent.txt")
    obs = executor(action)

    assert obs.is_error
    assert "not found" in obs.text.lower()


def test_read_file_directory(tmp_path):
    """Test reading a directory returns error."""
    # Create a directory
    test_dir = tmp_path / "testdir"
    test_dir.mkdir()

    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path="testdir")
    obs = executor(action)

    assert obs.is_error
    assert "directory" in obs.text.lower()


def test_read_file_absolute_path(tmp_path):
    """Test reading with absolute path."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content\n")

    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path=str(test_file))
    obs = executor(action)

    assert not obs.is_error
    assert "content" in obs.file_content


def test_read_file_offset_beyond_length(tmp_path):
    """Test reading with offset beyond file length."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("line 1\nline 2\n")

    executor = ReadFileExecutor(workspace_root=str(tmp_path))
    action = ReadFileAction(file_path="test.txt", offset=100)
    obs = executor(action)

    assert obs.is_error
    assert "beyond" in obs.text.lower()
