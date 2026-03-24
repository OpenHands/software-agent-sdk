"""Tests for Nemotron str_replace tool."""

from openhands.tools.nemotron.str_replace.definition import (
    StrReplaceAction,
    StrReplaceTool,
)
from openhands.tools.nemotron.str_replace.impl import StrReplaceExecutor


def test_str_replace_tool_name():
    """Test that StrReplaceTool has the correct name."""
    assert StrReplaceTool.name == "str_replace"


def test_str_replace_view_file(tmp_path):
    """Test viewing a file."""
    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    return 'hello'\n")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(command="view", path=str(test_file))
    obs = executor(action)

    assert not obs.is_error
    assert "def foo():" in obs.text


def test_str_replace_view_directory(tmp_path):
    """Test viewing a directory."""
    (tmp_path / "file1.txt").write_text("content1")
    (tmp_path / "file2.py").write_text("content2")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(command="view", path=str(tmp_path))
    obs = executor(action)

    assert not obs.is_error
    assert "file1.txt" in obs.text
    assert "file2.py" in obs.text


def test_str_replace_create_file(tmp_path):
    """Test creating a new file."""
    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(
        command="create", path=str(tmp_path / "new.py"), file_text="print('hello')\n"
    )
    obs = executor(action)

    assert not obs.is_error
    assert (tmp_path / "new.py").exists()
    assert (tmp_path / "new.py").read_text() == "print('hello')\n"


def test_str_replace_basic_replacement(tmp_path):
    """Test basic find/replace."""
    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    return 'old'\n")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(
        command="str_replace",
        path=str(test_file),
        old_str="'old'",
        new_str="'new'",
    )
    obs = executor(action)

    assert not obs.is_error
    assert test_file.read_text() == "def foo():\n    return 'new'\n"


def test_str_replace_string_not_found(tmp_path):
    """Test error when old_str is not found."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\n")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(
        command="str_replace",
        path=str(test_file),
        old_str="goodbye",
        new_str="farewell",
    )
    obs = executor(action)

    assert obs.is_error


def test_str_replace_insert(tmp_path):
    """Test inserting text after a line."""
    test_file = tmp_path / "test.py"
    test_file.write_text("line1\nline2\nline3\n")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(
        command="insert",
        path=str(test_file),
        insert_line=1,
        new_str="inserted",
    )
    obs = executor(action)

    assert not obs.is_error
    assert test_file.read_text() == "line1\ninserted\nline2\nline3\n"


def test_str_replace_undo_edit(tmp_path):
    """Test undoing the last edit."""
    test_file = tmp_path / "test.py"
    test_file.write_text("original content\n")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))

    # Make an edit
    action = StrReplaceAction(
        command="str_replace",
        path=str(test_file),
        old_str="original",
        new_str="modified",
    )
    executor(action)
    assert test_file.read_text() == "modified content\n"

    # Undo the edit
    action = StrReplaceAction(command="undo_edit", path=str(test_file))
    obs = executor(action)

    assert not obs.is_error
    assert test_file.read_text() == "original content\n"


def test_str_replace_create_existing_file_error(tmp_path):
    """Test error when trying to create file that already exists."""
    test_file = tmp_path / "existing.py"
    test_file.write_text("old content\n")

    executor = StrReplaceExecutor(workspace_root=str(tmp_path))
    action = StrReplaceAction(
        command="create", path=str(test_file), file_text="new content\n"
    )
    obs = executor(action)

    assert obs.is_error
    assert "already exists" in obs.text.lower()
