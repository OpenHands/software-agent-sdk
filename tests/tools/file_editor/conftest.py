import sys
import tempfile
import time
from pathlib import Path

import pytest

from openhands.sdk.tool.schema import TextContent
from openhands.tools.file_editor.definition import (
    FileEditorObservation,
)
from openhands.tools.file_editor.editor import FileEditor


@pytest.fixture
def temp_file():
    """Create a temporary file for testing."""
    # Create temp file and close the handle immediately to avoid locking on Windows
    with tempfile.NamedTemporaryFile(delete=False) as f:
        file_path = Path(f.name)
    # File handle is now closed, yield the path
    yield file_path
    # On Windows, files may be locked briefly after handles are closed.
    # This is due to Windows file system behavior where file handles may not be
    # immediately released, opportunistic locks (oplocks), or background processes
    # like File Explorer holding handles. See:
    # - https://learn.microsoft.com/en-us/windows-hardware/drivers/ifs/fsctl-opbatch-ack-close-pending
    # - https://learn.microsoft.com/en-us/answers/questions/5559106/file-explorer-is-opening-files-and-folders-prevent
    # Retry deletion with a small delay if it fails
    max_retries = 5 if sys.platform == "win32" else 1
    for attempt in range(max_retries):
        try:
            if file_path.exists():
                file_path.unlink()
            break
        except (FileNotFoundError, PermissionError):
            if attempt < max_retries - 1:
                time.sleep(0.1)  # Small delay before retry
            elif attempt == max_retries - 1:
                # Last attempt failed, but don't fail the test
                # The file will be cleaned up by the OS eventually
                pass


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def editor():
    """Create a FileEditor instance for testing."""
    return FileEditor()


@pytest.fixture
def editor_with_test_file(tmp_path):
    """Create a FileEditor instance with a test file."""
    editor = FileEditor()
    test_file = tmp_path / "test.txt"
    test_file.write_text("This is a test file.\nThis file is for testing purposes.")
    return editor, test_file


@pytest.fixture
def editor_python_file_with_tabs(tmp_path):
    """Create a FileEditor instance with a Python test file containing tabs."""
    editor = FileEditor()
    test_file = tmp_path / "test.py"
    test_file.write_text('def test():\n\tprint("Hello, World!")')
    return editor, test_file


def assert_successful_result(
    result: FileEditorObservation, expected_path: str | None = None
):
    """Assert that a result is successful (no error)."""
    assert isinstance(result, FileEditorObservation)
    assert not result.is_error
    if expected_path:
        assert result.path == expected_path


def assert_error_result(
    result: FileEditorObservation, expected_error_substring: str | None = None
):
    """Assert that a result contains an error."""
    assert isinstance(result, FileEditorObservation)
    assert result.is_error
    if expected_error_substring:
        content_text = (
            result.content
            if isinstance(result.content, str)
            else "".join([c.text for c in result.content if isinstance(c, TextContent)])
        )
        assert expected_error_substring in content_text


def create_test_file(path: Path, content: str):
    """Helper to create a test file with given content."""
    path.write_text(content)
    return path
