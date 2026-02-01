"""Tests for edit tool."""

from openhands.tools.gemini.edit.definition import EditAction
from openhands.tools.gemini.edit.impl import EditExecutor


def test_edit_basic_replacement(tmp_path):
    """Test basic find/replace."""
    # Create a test file
    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    return 'old'\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(file_path="test.py", old_string="'old'", new_string="'new'")
    obs = executor(action)

    assert not obs.is_error
    assert not obs.is_new_file
    assert obs.replacements_made == 1
    assert test_file.read_text() == "def foo():\n    return 'new'\n"


def test_edit_multiple_replacements(tmp_path):
    """Test replacing multiple occurrences."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("foo bar foo baz foo\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(
        file_path="test.txt",
        old_string="foo",
        new_string="qux",
        expected_replacements=3,
    )
    obs = executor(action)

    assert not obs.is_error
    assert obs.replacements_made == 3
    assert test_file.read_text() == "qux bar qux baz qux\n"


def test_edit_mismatch_expected_count(tmp_path):
    """Test error when replacement count doesn't match expected."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("foo bar foo\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(
        file_path="test.txt",
        old_string="foo",
        new_string="qux",
        expected_replacements=1,
    )
    obs = executor(action)

    assert obs.is_error
    assert "expected 1" in obs.text.lower()
    assert "found 2" in obs.text.lower()


def test_edit_create_new_file(tmp_path):
    """Test creating a new file with empty old_string."""
    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(
        file_path="new.py", old_string="", new_string="print('hello')\n"
    )
    obs = executor(action)

    assert not obs.is_error
    assert obs.is_new_file
    assert obs.replacements_made == 1

    # Verify file was created
    test_file = tmp_path / "new.py"
    assert test_file.exists()
    assert test_file.read_text() == "print('hello')\n"


def test_edit_create_existing_file_error(tmp_path):
    """Test error when trying to create file that already exists."""
    # Create existing file
    test_file = tmp_path / "existing.py"
    test_file.write_text("old content\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(
        file_path="existing.py", old_string="", new_string="new content\n"
    )
    obs = executor(action)

    assert obs.is_error
    assert "already exists" in obs.text.lower()


def test_edit_string_not_found(tmp_path):
    """Test error when old_string is not found."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(
        file_path="test.txt", old_string="goodbye", new_string="farewell"
    )
    obs = executor(action)

    assert obs.is_error
    assert "could not find" in obs.text.lower()
    assert "0 occurrences" in obs.text.lower()


def test_edit_identical_strings(tmp_path):
    """Test error when old_string and new_string are the same."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(file_path="test.txt", old_string="hello", new_string="hello")
    obs = executor(action)

    assert obs.is_error
    assert "no changes" in obs.text.lower()
    assert "identical" in obs.text.lower()


def test_edit_file_not_found(tmp_path):
    """Test error when file doesn't exist."""
    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(file_path="nonexistent.txt", old_string="old", new_string="new")
    obs = executor(action)

    assert obs.is_error
    assert "not found" in obs.text.lower()


def test_edit_multiline_replacement(tmp_path):
    """Test replacing multiline text."""
    test_file = tmp_path / "test.py"
    test_file.write_text("def foo():\n    print('old')\n    return 1\n")

    executor = EditExecutor(workspace_root=str(tmp_path))
    action = EditAction(
        file_path="test.py",
        old_string="    print('old')\n    return 1",
        new_string="    print('new')\n    return 2",
    )
    obs = executor(action)

    assert not obs.is_error
    assert obs.replacements_made == 1
    assert test_file.read_text() == "def foo():\n    print('new')\n    return 2\n"
