import tempfile
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
    with tempfile.NamedTemporaryFile(delete=False) as f:
        yield Path(f.name)
        try:
            Path(f.name).unlink()
        except FileNotFoundError:
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
