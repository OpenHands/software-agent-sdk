from pathlib import Path

import pytest

from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.exceptions import (
    EditorToolParameterInvalidError,
)


def test_workspace_root_as_cwd(tmp_path):
    """Test that workspace_root is used as the current working directory for
    path suggestions."""
    # Create a workspace root
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    # Create a file inside the workspace root
    test_file = workspace_root / "test.txt"
    test_file.write_text("This is a test file")

    # Initialize editor with workspace_root
    editor = FileEditor(workspace_root=str(workspace_root))

    # Test that a relative path suggestion uses the workspace_root
    relative_path = "test.txt"
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=relative_path)

    error_message = str(exc_info.value.message)
    assert "The path should be an absolute path" in error_message
    assert "Maybe you meant" in error_message

    # Extract the suggested path from the error message
    suggested_path = error_message.split("Maybe you meant ")[1].strip("?")
    assert Path(suggested_path).is_absolute()
    assert str(workspace_root) in suggested_path

    # Test with a non-existent file
    non_existent_path = "non_existent.txt"
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=non_existent_path)

    error_message = str(exc_info.value.message)
    assert "The path should be an absolute path" in error_message
    assert "Maybe you meant" not in error_message


def test_relative_workspace_root_do_not_raises_error(tmp_path, monkeypatch):
    """Test that a relative workspace_root raises a ValueError."""
    # Set up a directory structure
    current_dir = tmp_path / "current_dir"
    current_dir.mkdir()

    # Change to the current directory
    monkeypatch.chdir(current_dir)

    # Initialize editor with a relative workspace_root should not raise ValueError
    editor = FileEditor(workspace_root="workspace")
    assert editor._cwd == str(current_dir / "workspace")


def test_suggestion_when_no_workspace_root(tmp_path, monkeypatch):
    """Test that no path suggestion is made when workspace_root is not provided."""
    # Create a temporary file in the current directory
    current_dir = tmp_path / "current_dir"
    current_dir.mkdir()
    test_file = current_dir / "test.txt"
    test_file.write_text("This is a test file")

    # Set the current directory to our temporary directory
    monkeypatch.chdir(current_dir)

    # Initialize editor without workspace_root
    editor = FileEditor()

    # Test path suggestion should exists for existing files
    relative_path = "test.txt"
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=relative_path)

    error_message = str(exc_info.value.message)
    assert "The path should be an absolute path" in error_message
    assert "Maybe you meant" in error_message
    assert str(current_dir) in error_message

    # Test with a non-existent file (should also have no suggestion)
    non_existent_path = "non_existent.txt"
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=non_existent_path)

    error_message = str(exc_info.value.message)
    assert "The path should be an absolute path" in error_message
    assert "Maybe you meant" not in error_message
