"""Tests for error handling in file editor."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.impl import file_editor

from .conftest import assert_error_result


def test_validation_error_formatting():
    """Test that validation errors are properly formatted in the output."""
    result = file_editor(
        command="view",
        path="/nonexistent/file.txt",
    )
    assert_error_result(result)
    assert result.is_error and "does not exist" in result.text

    # Test directory validation for non-view commands
    result = file_editor(
        command="str_replace",
        path="/tmp",
        old_str="something",
        new_str="new",
    )
    assert_error_result(result)
    assert result.is_error and "directory and only the `view` command" in result.text


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only regression test")
def test_create_rejects_foreign_platform_absolute_paths(tmp_path, monkeypatch):
    """Create should reject absolute-path syntax that is not absolute on this host."""
    monkeypatch.chdir(tmp_path)
    result = file_editor(command="create", path=r"C:\foo", file_text="hello")

    assert_error_result(result)
    assert "absolute path" in result.text
    assert not (tmp_path / r"C:\foo").exists()


def test_str_replace_error_handling(temp_file):
    """Test error handling in str_replace command."""
    # Create a test file
    content = "line 1\nline 2\nline 3\n"
    with open(temp_file, "w") as f:
        f.write(content)

    # Test non-existent string
    result = file_editor(
        command="str_replace",
        path=temp_file,
        old_str="nonexistent",
        new_str="something",
    )
    assert_error_result(result)
    assert result.is_error and "did not appear verbatim" in result.text

    # Test multiple occurrences
    with open(temp_file, "w") as f:
        f.write("line\nline\nother")

    result = file_editor(
        command="str_replace",
        path=temp_file,
        old_str="line",
        new_str="new_line",
    )
    assert_error_result(result)
    assert result.is_error and "Multiple occurrences" in result.text
    assert result.is_error and "lines [1, 2]" in result.text


def test_view_range_validation(temp_file):
    """Test validation of view_range parameter."""
    # Create a test file
    content = "line 1\nline 2\nline 3\n"
    with open(temp_file, "w") as f:
        f.write(content)

    # Test invalid range format
    result = file_editor(
        command="view",
        path=temp_file,
        view_range=[1],  # Should be [start, end]
    )
    assert_error_result(result)
    assert result.is_error and "should be a list of two integers" in result.text

    # Test out of bounds range: should clamp to file end and show a warning
    result = file_editor(
        command="view",
        path=temp_file,
        view_range=[1, 10],  # File only has 3 lines
    )
    # This should succeed but show a warning
    assert not result.is_error
    assert (
        "NOTE: We only show up to 3 since there're only 3 lines in this file."
        in result.text
    )

    # Test invalid range order
    result = file_editor(
        command="view",
        path=temp_file,
        view_range=[3, 1],  # End before start
    )
    assert_error_result(result)
    assert result.is_error and "should be greater than or equal to" in result.text


def test_insert_validation(temp_file):
    """Test validation in insert command."""
    # Create a test file
    content = "line 1\nline 2\nline 3\n"
    with open(temp_file, "w") as f:
        f.write(content)

    # Test insert at negative line
    result = file_editor(
        command="insert",
        path=temp_file,
        insert_line=-1,
        new_str="new line",
    )
    assert_error_result(result)
    assert result.is_error and "should be within the range" in result.text

    # Test insert beyond file length
    result = file_editor(
        command="insert",
        path=temp_file,
        insert_line=10,
        new_str="new line",
    )
    assert_error_result(result)
    assert result.is_error and "should be within the range" in result.text


def test_undo_validation(temp_file):
    """Test undo_edit validation."""
    # Create a test file
    content = "line 1\nline 2\nline 3\n"
    with open(temp_file, "w") as f:
        f.write(content)

    # Try to undo without any previous edits
    result = file_editor(
        command="undo_edit",
        path=temp_file,
    )
    assert_error_result(result)
    assert result.is_error and "No edit history found" in result.text


def test_view_directory_permission_error_returns_error_observation():
    """Directory view should return an error observation on PermissionError."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        editor = FileEditor()
        with patch.object(
            editor,
            "_count_hidden_children",
            side_effect=PermissionError("denied"),
        ):
            result = editor.view(path)
        assert result.is_error
        assert "denied" in result.text


def test_view_subdirectory_permission_error_skips_inaccessible_dir():
    """Subdirectory permission errors should be silently skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        sub = path / "sub"
        sub.mkdir()
        (path / "visible.txt").write_text("hello")

        # Simulate iterdir on the subdirectory raising PermissionError.
        original_iterdir = Path.iterdir

        def patched_iterdir(self: Path):
            if self == sub:
                raise PermissionError("denied")
            return original_iterdir(self)

        editor = FileEditor()
        with patch.object(Path, "iterdir", patched_iterdir):
            result = editor.view(path)
        assert not result.is_error
        assert "visible.txt" in result.text
