"""Tests for GrepExecutor implementation.

These tests verify that grep behaves like OpenHands:
- Case-insensitive search (rg -i)
- Returns file paths only (rg -l)
- Sorted by modification time (--sortr=modified)
"""

import tempfile
import time
from pathlib import Path

import pytest

from openhands.tools.grep import GrepAction
from openhands.tools.grep.impl import GrepExecutor
from openhands.tools.utils import _check_ripgrep_available


def test_grep_executor_initialization():
    """Test that GrepExecutor initializes correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GrepExecutor(working_dir=temp_dir)
        assert executor.working_dir == Path(temp_dir).resolve()


def test_grep_executor_basic_search():
    """Test basic content search - returns file paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files
        (Path(temp_dir) / "app.py").write_text("print('hello')\nreturn 0")
        (Path(temp_dir) / "utils.py").write_text(
            "def helper():\n    print('Helper')\n    return True"
        )

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="print")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 2  # Two files containing "print"
        assert observation.pattern == "print"
        assert observation.search_path == str(Path(temp_dir).resolve())

        # Check that matches are file paths
        for file_path in observation.matches:
            assert isinstance(file_path, str)
            assert file_path.endswith(".py")
            assert Path(file_path).exists()


def test_grep_executor_case_insensitive():
    """Test that search is case-insensitive."""
    with tempfile.TemporaryDirectory() as temp_dir:
        content = "Print('uppercase')\nprint('lowercase')\nPRINT('allcaps')"
        (Path(temp_dir) / "case_test.py").write_text(content)

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="print")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1  # File contains pattern (case-insensitive)
        assert "case_test.py" in observation.matches[0]


def test_grep_executor_include_filter():
    """Test include pattern filtering."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("print('test')")
        (Path(temp_dir) / "test.js").write_text("console.log('test')")
        (Path(temp_dir) / "readme.md").write_text("# Test")

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="test", include="*.py")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1
        assert observation.matches[0].endswith(".py")


def test_grep_executor_custom_path():
    """Test search in custom directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        sub_dir = Path(temp_dir) / "subdir"
        sub_dir.mkdir()
        (sub_dir / "file.py").write_text("print('test')")
        (Path(temp_dir) / "other.py").write_text("print('test')")

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="print", path=str(sub_dir))
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1
        assert observation.search_path == str(sub_dir.resolve())
        assert str(sub_dir) in str(observation.matches[0])


def test_grep_executor_invalid_path():
    """Test search in invalid directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="test", path="/nonexistent/path")
        observation = executor(action)

        assert observation.is_error is True
        assert "not a valid directory" in observation.text


def test_grep_executor_no_matches():
    """Test when no files match the pattern."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("def main():\n    return 0")

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="nonexistent")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 0


def test_grep_executor_hidden_files_excluded():
    """Test that hidden files are excluded."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "visible.py").write_text("test")
        (Path(temp_dir) / ".hidden.py").write_text("test")

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="test")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1
        assert ".hidden" not in observation.matches[0]


@pytest.mark.skipif(
    not _check_ripgrep_available(),
    reason="ripgrep not available - sorting test requires ripgrep",
)
def test_grep_executor_sorting():
    """Test that files are sorted by modification time (newest first)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        old_file = Path(temp_dir) / "old.py"
        new_file = Path(temp_dir) / "new.py"

        old_file.write_text("test")
        time.sleep(0.01)
        new_file.write_text("test")

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="test")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 2
        # Newest file should be first
        assert "new.py" in observation.matches[0]
        assert "old.py" in observation.matches[1]


def test_grep_executor_truncation():
    """Test that results are truncated to 100 files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create 150 files
        for i in range(150):
            (Path(temp_dir) / f"file{i}.py").write_text("test")

        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="test")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 100
        assert observation.truncated is True


def test_grep_executor_invalid_regex():
    """Test handling of invalid regex patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GrepExecutor(working_dir=temp_dir)
        action = GrepAction(pattern="[invalid")
        observation = executor(action)

        assert observation.is_error is True
        assert "Invalid regex pattern" in observation.text
