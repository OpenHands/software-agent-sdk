"""Tests for base_path enforcement in FileEditor."""

import logging

import pytest

from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.exceptions import (
    EditorToolParameterInvalidError,
)


def test_base_path_enforcement_view_outside(tmp_path):
    """Test that viewing files outside base_path is rejected."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file outside base_path
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("This is outside")

    editor = FileEditor(base_path=str(base))

    # Try to view file outside base_path
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(outside_file))

    assert "outside the allowed base path" in str(exc_info.value.message)
    assert str(base) in str(exc_info.value.message)


def test_base_path_enforcement_create_outside(tmp_path):
    """Test that creating files outside base_path is rejected."""
    base = tmp_path / "base"
    base.mkdir()

    # Try to create a file outside base_path
    outside_file = tmp_path / "new_file.txt"

    editor = FileEditor(base_path=str(base))

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="create", path=str(outside_file), file_text="content")

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_enforcement_str_replace_outside(tmp_path):
    """Test that str_replace outside base_path is rejected."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file outside base_path
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("old content")

    editor = FileEditor(base_path=str(base))

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(
            command="str_replace",
            path=str(outside_file),
            old_str="old",
            new_str="new",
        )

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_enforcement_insert_outside(tmp_path):
    """Test that insert outside base_path is rejected."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file outside base_path
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("line 1\n")

    editor = FileEditor(base_path=str(base))

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(
            command="insert",
            path=str(outside_file),
            insert_line=1,
            new_str="inserted",
        )

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_enforcement_undo_outside(tmp_path):
    """Test that undo_edit outside base_path is rejected."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file outside base_path
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("content")

    editor = FileEditor(base_path=str(base))

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="undo_edit", path=str(outside_file))

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_allows_operations_inside(tmp_path):
    """Test that operations inside base_path are allowed."""
    base = tmp_path / "base"
    base.mkdir()

    editor = FileEditor(base_path=str(base))

    # Create file inside base_path
    inside_file = base / "inside.txt"
    result = editor(command="create", path=str(inside_file), file_text="content")
    assert not result.is_error
    assert inside_file.exists()

    # View file inside base_path
    result = editor(command="view", path=str(inside_file))
    assert not result.is_error

    # Edit file inside base_path
    result = editor(
        command="str_replace",
        path=str(inside_file),
        old_str="content",
        new_str="new content",
    )
    assert not result.is_error


def test_base_path_parent_directory_traversal(tmp_path):
    """Test that parent directory traversal is blocked."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file in parent directory
    parent_file = tmp_path / "secret.txt"
    parent_file.write_text("secret content")

    editor = FileEditor(base_path=str(base))

    # Try to access parent using ../
    traversal_path = base / ".." / "secret.txt"

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(traversal_path))

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_symlink_escape_attempt(tmp_path):
    """Test that symlink escapes are blocked."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file outside base_path
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content")

    # Create a symlink inside base pointing outside
    symlink = base / "link_to_outside.txt"
    symlink.symlink_to(outside_file)

    editor = FileEditor(base_path=str(base))

    # Try to access the symlink (should be blocked because it resolves outside)
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(symlink))

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_symlink_inside_allowed(tmp_path):
    """Test that symlinks within base_path are allowed."""
    base = tmp_path / "base"
    base.mkdir()

    # Create a file inside base_path
    inside_file = base / "inside.txt"
    inside_file.write_text("inside content")

    # Create a symlink inside base pointing to another file in base
    symlink = base / "link_to_inside.txt"
    symlink.symlink_to(inside_file)

    editor = FileEditor(base_path=str(base))

    # Access via symlink should work
    result = editor(command="view", path=str(symlink))
    assert not result.is_error
    assert "inside content" in result.text


def test_base_path_nested_directory_allowed(tmp_path):
    """Test that nested directories within base_path are allowed."""
    base = tmp_path / "base"
    base.mkdir()

    # Create nested directory
    nested_dir = base / "subdir" / "nested"
    nested_dir.mkdir(parents=True)

    editor = FileEditor(base_path=str(base))

    # Create file in nested directory
    nested_file = nested_dir / "file.txt"
    result = editor(command="create", path=str(nested_file), file_text="nested content")
    assert not result.is_error
    assert nested_file.exists()


def test_base_path_absolute_path_outside(tmp_path):
    """Test that absolute paths outside base_path are rejected."""
    base = tmp_path / "base"
    base.mkdir()

    editor = FileEditor(base_path=str(base))

    # Try to access /etc/passwd
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path="/etc/passwd")

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_none_allows_all_paths(tmp_path):
    """Test that base_path=None allows all paths."""
    # Create files in different locations
    file1 = tmp_path / "dir1" / "file1.txt"
    file1.parent.mkdir()
    file1.write_text("content 1")

    file2 = tmp_path / "dir2" / "file2.txt"
    file2.parent.mkdir()
    file2.write_text("content 2")

    # Editor without base_path restriction
    editor = FileEditor(base_path=None)

    # Both files should be accessible
    result1 = editor(command="view", path=str(file1))
    assert not result1.is_error

    result2 = editor(command="view", path=str(file2))
    assert not result2.is_error


def test_base_path_view_directory_outside(tmp_path):
    """Test that viewing directories outside base_path is rejected."""
    base = tmp_path / "base"
    base.mkdir()

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    editor = FileEditor(base_path=str(base))

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(outside_dir))

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_view_directory_inside(tmp_path):
    """Test that viewing directories inside base_path is allowed."""
    base = tmp_path / "base"
    base.mkdir()

    inside_dir = base / "subdir"
    inside_dir.mkdir()

    # Create some files
    (inside_dir / "file1.txt").write_text("content 1")
    (inside_dir / "file2.txt").write_text("content 2")

    editor = FileEditor(base_path=str(base))

    result = editor(command="view", path=str(inside_dir))
    assert not result.is_error
    assert "file1.txt" in result.text
    assert "file2.txt" in result.text


def test_base_path_relative_conversion(tmp_path, monkeypatch):
    """Test that relative base_path is converted to absolute."""
    current_dir = tmp_path / "current"
    current_dir.mkdir()

    base = current_dir / "base"
    base.mkdir()

    # Change to current directory
    monkeypatch.chdir(current_dir)

    # Initialize with relative path
    editor = FileEditor(base_path="base")

    # The base_path should be resolved to absolute
    assert editor._base_path is not None
    assert editor._base_path.is_absolute()

    # Create file inside base
    inside_file = base / "file.txt"
    result = editor(command="create", path=str(inside_file), file_text="content")
    assert not result.is_error


def test_base_path_error_message_shows_base(tmp_path):
    """Test that error messages clearly show the base_path restriction."""
    base = tmp_path / "base"
    base.mkdir()

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("content")

    editor = FileEditor(base_path=str(base))

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(outside_file))

    error_msg = str(exc_info.value.message)
    assert str(outside_file) in error_msg or "Path" in error_msg
    assert str(base) in error_msg
    assert "outside the allowed base path" in error_msg


def test_base_path_complex_traversal(tmp_path):
    """Test complex parent directory traversal patterns."""
    base = tmp_path / "base" / "subdir"
    base.mkdir(parents=True)

    # Create a file outside base_path
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret")

    editor = FileEditor(base_path=str(base))

    # Try complex traversal: base/subdir/../../secret.txt
    complex_path = base / ".." / ".." / "secret.txt"

    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(complex_path))

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_boundary_case(tmp_path):
    """Test accessing the base_path directory itself."""
    base = tmp_path / "base"
    base.mkdir()

    editor = FileEditor(base_path=str(base))

    # Viewing the base directory itself should be allowed
    result = editor(command="view", path=str(base))
    assert not result.is_error


def test_base_path_with_workspace_root(tmp_path):
    """Test that base_path and workspace_root can coexist."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    base = tmp_path / "base"
    base.mkdir()

    # Create file in workspace
    workspace_file = workspace / "file.txt"
    workspace_file.write_text("workspace content")

    # Create file in base
    base_file = base / "file.txt"
    base_file.write_text("base content")

    # Editor with both workspace_root and base_path
    editor = FileEditor(workspace_root=str(workspace), base_path=str(base))

    # Should be able to access base_file (within base_path)
    result = editor(command="view", path=str(base_file))
    assert not result.is_error

    # Should NOT be able to access workspace_file (outside base_path)
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=str(workspace_file))

    assert "outside the allowed base path" in str(exc_info.value.message)


def test_base_path_logging(tmp_path, caplog):
    """Test that base_path restriction is logged on initialization."""
    caplog.set_level(logging.INFO)

    base = tmp_path / "base"
    base.mkdir()

    _ = FileEditor(base_path=str(base))

    # Check that initialization was logged
    assert "FileEditor initialized" in caplog.text
    assert "base path restriction enabled" in caplog.text
    assert str(base) in caplog.text


def test_base_path_none_no_restriction_log(caplog):
    """Test that no restriction message is logged when base_path is None."""
    caplog.set_level(logging.INFO)

    _ = FileEditor(base_path=None)

    # Check that only basic initialization is logged
    assert "FileEditor initialized" in caplog.text
    assert "base path restriction" not in caplog.text
