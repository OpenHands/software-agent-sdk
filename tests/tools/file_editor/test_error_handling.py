"""Tests for error handling in file editor."""

import sys

from openhands.tools.file_editor.impl import file_editor

from .conftest import assert_error_result


def test_validation_error_formatting():
    """Test that validation errors are properly formatted in the output."""
    # Use platform-appropriate nonexistent absolute path
    if sys.platform == "win32":
        nonexistent_path = "C:\\nonexistent\\file.txt"
        temp_dir = "C:\\Windows\\Temp"
    else:
        nonexistent_path = "/nonexistent/file.txt"
        temp_dir = "/tmp"

    result = file_editor(
        command="view",
        path=nonexistent_path,
    )
    assert_error_result(result)
    assert result.is_error and "does not exist" in result.text

    # Test directory validation for non-view commands
    result = file_editor(
        command="str_replace",
        path=temp_dir,
        old_str="something",
        new_str="new",
    )
    assert_error_result(result)
    assert result.is_error and "directory and only the `view` command" in result.text


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
