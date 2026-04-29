"""Tests for basic file editor operations."""

from pathlib import Path

import pytest

from openhands.tools.file_editor import (
    FileEditorObservation,
    file_editor,
)
from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.exceptions import (
    EditorToolParameterInvalidError,
    EditorToolParameterMissingError,
    ToolError,
)
from openhands.tools.file_editor.utils.constants import (
    DIRECTORY_CONTENT_TRUNCATED_NOTICE,
    TEXT_FILE_CONTENT_TRUNCATED_NOTICE,
)

from .conftest import (
    assert_successful_result,
)


@pytest.fixture
def editor(tmp_path):
    editor = FileEditor()
    # Set up a temporary directory with test files
    test_file = tmp_path / "test.txt"
    test_file.write_text("This is a test file.\nThis file is for testing purposes.")
    return editor, test_file


@pytest.fixture
def editor_python_file_with_tabs(tmp_path):
    editor = FileEditor()
    # Set up a temporary directory with test files
    test_file = tmp_path / "test.py"
    test_file.write_text('def test():\n\tprint("Hello, World!")')
    return editor, test_file


def test_file_editor_happy_path(temp_file):
    """Test basic str_replace operation."""
    old_str = "test file"
    new_str = "sample file"

    # Create test file
    with open(temp_file, "w") as f:
        f.write("This is a test file.\nThis file is for testing purposes.")

    # Call the `file_editor` function
    result = file_editor(
        command="str_replace",
        path=str(temp_file),
        old_str=old_str,
        new_str=new_str,
    )

    # Validate the result
    assert_successful_result(result, str(temp_file))
    assert (
        result.text is not None
        and "The file" in result.text
        and "has been edited" in result.text
    )
    assert result.text is not None and "This is a sample file." in result.text
    assert result.path == str(temp_file)
    assert result.prev_exist is True
    assert (
        result.old_content == "This is a test file.\nThis file is for testing purposes."
    )
    assert (
        result.new_content
        == "This is a sample file.\nThis file is for testing purposes."
    )

    # Ensure the file content was updated
    with open(temp_file) as f:
        content = f.read()
    assert "This is a sample file." in content


def test_file_editor_view_operation(temp_file):
    """Test view operation with file containing special content."""
    # Create content that includes various patterns
    xml_content = """This is a file with XML tags parsing logic...
match = re.search(
    r'<oh_aci_output_[0-9a-f]{32}>(.*?)</oh_aci_output_[0-9a-f]{32}>',
    result,
    re.DOTALL,
)
...More text here.
"""

    with open(temp_file, "w") as f:
        f.write(xml_content)

    result = file_editor(
        command="view",
        path=str(temp_file),
    )

    # Validate the result
    assert_successful_result(result, str(temp_file))
    assert (
        result.text is not None
        and "Here's the result of running `cat -n`" in result.text
    )
    assert (
        result.text is not None
        and "This is a file with XML tags parsing logic..." in result.text
    )
    assert result.text is not None and "match = re.search(" in result.text
    assert result.text is not None and "...More text here." in result.text


def test_successful_operations(temp_file):
    """Test successful file operations and their output formatting."""
    # Create a test file
    content = "line 1\nline 2\nline 3\n"
    with open(temp_file, "w") as f:
        f.write(content)

    # Test view
    result = file_editor(
        command="view",
        path=str(temp_file),
    )
    assert_successful_result(result)
    assert (
        result.text is not None
        and "Here's the result of running `cat -n`" in result.text
    )
    assert result.text is not None and "line 1" in result.text

    # Test str_replace
    result = file_editor(
        command="str_replace",
        path=str(temp_file),
        old_str="line 2",
        new_str="replaced line",
    )
    assert_successful_result(result)
    assert result.text is not None and "has been edited" in result.text
    assert result.text is not None and "replaced line" in result.text

    # Test insert
    result = file_editor(
        command="insert",
        path=str(temp_file),
        insert_line=1,
        new_str="inserted line",
    )
    assert_successful_result(result)
    assert result.text is not None and "has been edited" in result.text
    assert result.text is not None and "inserted line" in result.text


def test_tab_expansion(temp_file):
    """Test that tabs are properly handled in file operations."""
    # Create a file with tabs
    content = "no tabs\n\tindented\nline\twith\ttabs\n"
    with open(temp_file, "w") as f:
        f.write(content)

    # Test view command
    result = file_editor(
        command="view",
        path=str(temp_file),
    )
    assert_successful_result(result)
    # Tabs should be preserved in output
    assert result.text is not None and "\tindented" in result.text
    assert result.text is not None and "line\twith\ttabs" in result.text

    # Test str_replace with tabs in old_str
    result = file_editor(
        command="str_replace",
        path=str(temp_file),
        old_str="line\twith\ttabs",
        new_str="replaced line",
    )
    assert_successful_result(result)
    assert result.text is not None and "replaced line" in result.text

    # Test str_replace with tabs in new_str
    result = file_editor(
        command="str_replace",
        path=str(temp_file),
        old_str="replaced line",
        new_str="new\tline\twith\ttabs",
    )
    assert_successful_result(result)
    assert result.text is not None and "new\tline\twith\ttabs" in result.text

    # Test insert with tabs
    result = file_editor(
        command="insert",
        path=str(temp_file),
        insert_line=1,
        new_str="\tindented\tline",
    )
    assert_successful_result(result)
    assert result.text is not None and "\tindented\tline" in result.text


def test_create_operation(temp_file):
    """Test file creation operation."""
    # Remove the temp file first
    temp_file.unlink()

    content = "This is a new file.\nWith multiple lines."

    result = file_editor(
        command="create",
        path=str(temp_file),
        file_text=content,
    )

    assert_successful_result(result, str(temp_file))
    assert result.text is not None and "created successfully" in result.text
    assert result.prev_exist is False
    assert result.new_content == content

    # Verify file was created with correct content
    with open(temp_file) as f:
        file_content = f.read()
    assert file_content == content


def test_view_operation_truncation(temp_file):
    """Test that view operation truncates large files correctly."""
    from openhands.tools.file_editor.utils.constants import (
        MAX_RESPONSE_LEN_CHAR,
        TEXT_FILE_CONTENT_TRUNCATED_NOTICE,
    )

    # Create a large file that exceeds the str_replace_editor's truncation limit
    large_content = "A" * (MAX_RESPONSE_LEN_CHAR + 1000)
    with open(temp_file, "w") as f:
        f.write(large_content)

    # Test view command
    result = file_editor(
        command="view",
        path=str(temp_file),
    )

    assert_successful_result(result)
    assert result.text is not None

    # Check that truncation notice is present
    assert TEXT_FILE_CONTENT_TRUNCATED_NOTICE in result.text

    # The content should be truncated before line numbers are added
    # So the final output will be longer than MAX_RESPONSE_LEN_CHAR due to formatting
    # but the original content was truncated
    assert "Here's the result of running `cat -n`" in result.text

    # With head-and-tail truncation, should contain both start and end content
    # The line numbers will show as "     1\tA..." at start and end with "A"
    assert "\tA" in result.text  # Should have A's with tab formatting


def test_view_file(editor):
    editor, test_file = editor
    result = editor(command="view", path=str(test_file))
    assert isinstance(result, FileEditorObservation)
    assert f"Here's the result of running `cat -n` on {test_file}:" in result.text
    assert "1\tThis is a test file." in result.text
    assert "2\tThis file is for testing purposes." in result.text
    assert "3\t" not in result.text  # No extra line


def test_view_directory(editor):
    editor, test_file = editor
    parent_dir = test_file.parent
    result = editor(command="view", path=str(parent_dir))
    assert (
        result.text
        == f"""Here's the files and directories up to 2 levels deep in {parent_dir}, excluding hidden items:
{parent_dir}/
{parent_dir}/test.txt"""  # noqa: E501
    )


def test_view_with_a_specific_range(editor):
    editor, test_file = editor

    # Replace the current content with content: Line {line_number}
    _ = editor(
        command="str_replace",
        path=str(test_file),
        old_str="This is a test file.\nThis file is for testing purposes.",
        new_str="",
    )
    for i in range(0, 200):
        _ = editor(
            command="insert",
            path=str(test_file),
            insert_line=i,
            new_str=f"Line {i + 1}",
        )

    # View file in range 50-100
    result = editor(command="view", path=str(test_file), view_range=[50, 100])
    assert f"Here's the result of running `cat -n` on {test_file}:" in result.text
    assert "    49\tLine 49" not in result.text
    assert "    50\tLine 50" in result.text
    assert "   100\tLine 100" in result.text
    assert "101" not in result.text


def test_create_file(editor):
    editor, test_file = editor
    new_file = test_file.parent / "new_file.txt"
    result = editor(command="create", path=str(new_file), file_text="New file content")
    assert new_file.exists()
    assert new_file.read_text() == "New file content"
    assert "File created successfully" in result.text


def test_create_with_empty_string(editor):
    editor, test_file = editor
    new_file = test_file.parent / "empty_content.txt"
    result = editor(command="create", path=str(new_file), file_text="")
    assert new_file.exists()
    assert new_file.read_text() == ""
    assert "File created successfully" in result.text

    # Test the view command showing an empty line
    result = editor(command="view", path=str(new_file))
    assert f"Here's the result of running `cat -n` on {new_file}:" in result.text
    assert "1\t" in result.text  # Check for empty line


def test_create_with_none_file_text(editor):
    editor, test_file = editor
    new_file = test_file.parent / "none_content.txt"
    with pytest.raises(EditorToolParameterMissingError) as exc_info:
        editor(command="create", path=str(new_file), file_text=None)
    assert "file_text" in str(exc_info.value.message)


def test_str_replace_no_linting(editor):
    editor, test_file = editor
    result = editor(
        command="str_replace",
        path=str(test_file),
        old_str="test file",
        new_str="sample file",
    )
    assert isinstance(result, FileEditorObservation)

    # Test str_replace command
    assert (
        result.text
        == f"""The file {test_file} has been edited. Here's the result of running `cat -n` on a snippet of {test_file}:
     1\tThis is a sample file.
     2\tThis file is for testing purposes.
Review the changes and make sure they are as expected. Edit the file again if necessary."""  # noqa: E501
    )

    # Test that the file content has been updated
    assert "This is a sample file." in test_file.read_text()


def test_str_replace_multi_line_no_linting(editor):
    editor, test_file = editor
    result = editor(
        command="str_replace",
        path=str(test_file),
        old_str="This is a test file.\nThis file is for testing purposes.",
        new_str="This is a sample file.\nThis file is for testing purposes.",
    )
    assert isinstance(result, FileEditorObservation)

    # Test str_replace command
    assert (
        result.text
        == f"""The file {test_file} has been edited. Here's the result of running `cat -n` on a snippet of {test_file}:
     1\tThis is a sample file.
     2\tThis file is for testing purposes.
Review the changes and make sure they are as expected. Edit the file again if necessary."""  # noqa: E501
    )


def test_str_replace_multi_line_with_tabs_no_linting(editor_python_file_with_tabs):
    editor, test_file = editor_python_file_with_tabs
    result = editor(
        command="str_replace",
        path=str(test_file),
        old_str='def test():\n\tprint("Hello, World!")',
        new_str='def test():\n\tprint("Hello, Universe!")',
    )
    assert isinstance(result, FileEditorObservation)

    assert (
        result.text
        == f"""The file {test_file} has been edited. Here's the result of running `cat -n` on a snippet of {test_file}:
     1\tdef test():
     2\t\tprint("Hello, Universe!")
Review the changes and make sure they are as expected. Edit the file again if necessary."""  # noqa: E501
    )


def test_str_replace_error_multiple_occurrences(editor):
    editor, test_file = editor
    with pytest.raises(ToolError) as exc_info:
        editor(
            command="str_replace", path=str(test_file), old_str="test", new_str="sample"
        )
    assert "Multiple occurrences of old_str `test`" in str(exc_info.value.message)
    assert "[1, 2]" in str(exc_info.value.message)  # Should show both line numbers


def test_str_replace_error_multiple_multiline_occurrences(editor):
    editor, test_file = editor
    # Create a file with two identical multi-line blocks
    multi_block = """def example():
    print("Hello")
    return True"""
    content = f"{multi_block}\n\nprint('separator')\n\n{multi_block}"
    test_file.write_text(content)

    with pytest.raises(ToolError) as exc_info:
        editor(
            command="str_replace",
            path=str(test_file),
            old_str=multi_block,
            new_str='def new():\n    print("World")',
        )
    error_msg = str(exc_info.value.message)
    assert "Multiple occurrences of old_str" in error_msg
    assert "[1, 7]" in error_msg  # Should show correct starting line numbers


def test_str_replace_nonexistent_string(editor):
    editor, test_file = editor
    with pytest.raises(ToolError) as exc_info:
        editor(
            command="str_replace",
            path=str(test_file),
            old_str="Non-existent Line",
            new_str="New Line",
        )
    assert "No replacement was performed" in str(exc_info)
    assert f"old_str `Non-existent Line` did not appear verbatim in {test_file}" in str(
        exc_info.value.message
    )


def test_str_replace_with_empty_new_str(editor):
    editor, test_file = editor
    test_file.write_text("Line 1\nLine to remove\nLine 3")
    result = editor(
        command="str_replace",
        path=str(test_file),
        old_str="Line to remove\n",
        new_str="",
    )
    assert isinstance(result, FileEditorObservation)
    assert test_file.read_text() == "Line 1\nLine 3"


def test_str_replace_with_empty_old_str(editor):
    editor, test_file = editor
    test_file.write_text("Line 1\nLine 2\nLine 3")
    with pytest.raises(ToolError) as exc_info:
        editor(
            command="str_replace",
            path=str(test_file),
            old_str="",
            new_str="New string",
        )
    assert (
        str(exc_info.value.message)
        == """No replacement was performed. Multiple occurrences of old_str `` in lines [1, 2, 3]. Please ensure it is unique."""  # noqa: E501
    )


def test_str_replace_with_none_old_str(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterMissingError) as exc_info:
        editor(
            command="str_replace",
            path=str(test_file),
            old_str=None,
            new_str="new content",
        )
    assert "old_str" in str(exc_info.value.message)


def test_insert_no_linting(editor):
    editor, test_file = editor
    result = editor(
        command="insert", path=str(test_file), insert_line=1, new_str="Inserted line"
    )
    assert isinstance(result, FileEditorObservation)
    assert "Inserted line" in test_file.read_text()
    assert (
        result.text
        == f"""The file {test_file} has been edited. Here's the result of running `cat -n` on a snippet of the edited file:
     1\tThis is a test file.
     2\tInserted line
     3\tThis file is for testing purposes.
Review the changes and make sure they are as expected (correct indentation, no duplicate lines, etc). Edit the file again if necessary."""  # noqa: E501
    )


def test_insert_invalid_line(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(
            command="insert",
            path=str(test_file),
            insert_line=10,
            new_str="Invalid Insert",
        )
    assert "Invalid `insert_line` parameter" in str(exc_info.value.message)
    assert "It should be within the range of allowed values:" in str(
        exc_info.value.message
    )


def test_insert_with_empty_string(editor):
    editor, test_file = editor
    result = editor(
        command="insert",
        path=str(test_file),
        insert_line=1,
        new_str="",
    )
    assert isinstance(result, FileEditorObservation)
    content = test_file.read_text().splitlines()
    assert "" in content
    assert len(content) == 3  # Original 2 lines plus empty line


def test_insert_chinese_text_into_english_file(editor):
    editor, test_file = editor
    result = editor(
        command="insert",
        path=str(test_file),
        insert_line=0,
        new_str="中文文本",
    )
    assert isinstance(result, FileEditorObservation)
    assert "中文文本" in test_file.read_text()
    assert (
        result.text
        == f"""The file {test_file} has been edited. Here's the result of running `cat -n` on a snippet of the edited file:
     1\t中文文本
     2\tThis is a test file.
     3\tThis file is for testing purposes.
Review the changes and make sure they are as expected (correct indentation, no duplicate lines, etc). Edit the file again if necessary."""  # noqa: E501
    )


def test_insert_with_none_new_str(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterMissingError) as exc_info:
        editor(
            command="insert",
            path=str(test_file),
            insert_line=1,
            new_str=None,
        )
    assert "new_str" in str(exc_info.value.message)


def test_validate_path_invalid(editor):
    editor, test_file = editor
    invalid_file = test_file.parent / "nonexistent.txt"
    with pytest.raises(EditorToolParameterInvalidError):
        editor(command="view", path=str(invalid_file))


def test_create_existing_file_error(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterInvalidError):
        editor(command="create", path=str(test_file), file_text="New content")


def test_str_replace_missing_old_str(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterMissingError):
        editor(command="str_replace", path=str(test_file), new_str="sample")


def test_str_replace_new_str_and_old_str_same(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(
            command="str_replace",
            path=str(test_file),
            old_str="test file",
            new_str="test file",
        )
    assert (
        "No replacement was performed. `new_str` and `old_str` must be different."
        in str(exc_info.value.message)
    )


def test_insert_missing_line_param(editor):
    editor, test_file = editor
    with pytest.raises(EditorToolParameterMissingError):
        editor(command="insert", path=str(test_file), new_str="Missing insert line")


def test_view_directory_with_hidden_files(tmp_path):
    editor = FileEditor()

    # Create a directory with some test files
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    (test_dir / "visible.txt").write_text("content1")
    (test_dir / ".hidden1").write_text("hidden1")
    (test_dir / ".hidden2").write_text("hidden2")

    # Create a hidden subdirectory with a file
    hidden_subdir = test_dir / ".hidden_dir"
    hidden_subdir.mkdir()
    (hidden_subdir / "file.txt").write_text("content3")

    # Create a visible subdirectory
    visible_subdir = test_dir / "visible_dir"
    visible_subdir.mkdir()

    # View the directory
    result = editor(command="view", path=str(test_dir))

    # Verify output
    assert isinstance(result, FileEditorObservation)
    assert str(test_dir) in result.text
    assert "visible.txt" in result.text  # Visible file is shown
    assert "visible_dir" in result.text  # Visible directory is shown
    assert ".hidden1" not in result.text  # Hidden files not shown
    assert ".hidden2" not in result.text
    assert ".hidden_dir" not in result.text
    assert (
        "3 hidden files/directories in this directory are excluded" in result.text
    )  # Shows count of hidden items in current dir only
    assert "ls -la" in result.text  # Shows command to view hidden files


def test_view_symlinked_directory(tmp_path):
    editor = FileEditor()

    # Create a directory with some test files
    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()
    (source_dir / "file1.txt").write_text("content1")
    (source_dir / "file2.txt").write_text("content2")

    # Create a subdirectory with a file
    subdir = source_dir / "subdir"
    subdir.mkdir()
    (subdir / "file3.txt").write_text("content3")

    # Create a symlink to the directory
    symlink_dir = tmp_path / "symlink_dir"
    symlink_dir.symlink_to(source_dir)

    # View the symlinked directory
    result = editor(command="view", path=str(symlink_dir))

    # Verify that all files are listed through the symlink
    assert isinstance(result, FileEditorObservation)
    assert str(symlink_dir) in result.text
    assert "file1.txt" in result.text
    assert "file2.txt" in result.text
    assert "subdir" in result.text
    assert "file3.txt" in result.text


def test_view_large_directory_with_truncation(editor, tmp_path):
    editor, _ = editor
    # Create a directory with many files to trigger truncation
    large_dir = tmp_path / "large_dir"
    large_dir.mkdir()
    for i in range(1000):  # 1000 files should trigger truncation
        (large_dir / f"file_{i}.txt").write_text("content")

    result = editor(command="view", path=str(large_dir))
    assert isinstance(result, FileEditorObservation)
    assert DIRECTORY_CONTENT_TRUNCATED_NOTICE in result.text


def test_view_directory_on_hidden_path(tmp_path):
    """Directory structure:
    .test_dir/
    ├── visible1.txt
    ├── .hidden1
    ├── visible_dir/
    │   ├── visible2.txt
    │   └── .hidden2
    └── .hidden_dir/
        ├── visible3.txt
        └── .hidden3
    """

    editor = FileEditor()

    # Create a directory with test files at depth 1
    hidden_test_dir = tmp_path / ".hidden_test_dir"
    hidden_test_dir.mkdir()
    (hidden_test_dir / "visible1.txt").write_text("content1")
    (hidden_test_dir / ".hidden1").write_text("hidden1")

    # Create a visible subdirectory with visible and hidden files
    visible_subdir = hidden_test_dir / "visible_dir"
    visible_subdir.mkdir()
    (visible_subdir / "visible2.txt").write_text("content2")
    (visible_subdir / ".hidden2").write_text("hidden2")

    # Create a hidden subdirectory with visible and hidden files
    hidden_subdir = hidden_test_dir / ".hidden_dir"
    hidden_subdir.mkdir()
    (hidden_subdir / "visible3.txt").write_text("content3")
    (hidden_subdir / ".hidden3").write_text("hidden3")

    # View the directory
    result = editor(command="view", path=str(hidden_test_dir))

    # Verify output
    assert isinstance(result, FileEditorObservation)
    # Depth 1: Visible files/dirs shown, hidden files/dirs not shown
    assert "visible1.txt" in result.text
    assert "visible_dir" in result.text
    assert ".hidden1" not in result.text
    assert ".hidden_dir" not in result.text

    # Depth 2: Files in visible_dir shown
    assert "visible2.txt" in result.text
    assert ".hidden2" not in result.text

    # Depth 2: Files in hidden_dir not shown
    assert "visible3.txt" not in result.text
    assert ".hidden3" not in result.text

    # Hidden file count only includes depth 1
    assert (
        "2 hidden files/directories in this directory are excluded" in result.text
    )  # Only .hidden1 and .hidden_dir at depth 1


def test_view_large_file_with_truncation(editor, tmp_path):
    editor, _ = editor
    # Create a large file to trigger truncation
    large_file = tmp_path / "large_test.txt"
    large_content = "Line 1\n" * 16000  # 16000 lines should trigger truncation
    large_file.write_text(large_content)

    result = editor(command="view", path=str(large_file))
    assert isinstance(result, FileEditorObservation)
    assert TEXT_FILE_CONTENT_TRUNCATED_NOTICE in result.text


def test_validate_path_suggests_absolute_path(editor, tmp_path):
    editor, test_file = editor

    # Since the editor fixture doesn't set workspace_root,
    # we should not get a suggestion
    relative_path = test_file.name  # This is a relative path
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        editor(command="view", path=relative_path)
    error_message = str(exc_info.value.message)
    assert "The path should be an absolute path" in error_message
    assert "Maybe you meant" not in error_message

    # Now create an editor with workspace_root
    workspace_editor = FileEditor(workspace_root=str(test_file.parent))

    # We should get a suggestion now
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        workspace_editor(command="view", path=relative_path)
    error_message = str(exc_info.value.message)
    assert "The path should be an absolute path" in error_message
    assert "Maybe you meant" in error_message
    suggested_path = error_message.split("Maybe you meant ")[1].strip("?")
    assert Path(suggested_path).is_absolute()
    assert str(test_file.parent) in suggested_path


def test_str_replace_and_insert_snippet_output_on_a_large_file(editor):
    editor, test_file = editor

    # Replace the current content with content: Line {line_number}
    _ = editor(
        command="str_replace",
        path=str(test_file),
        old_str="This is a test file.\nThis file is for testing purposes.",
        new_str="",
    )
    for i in range(0, 700):
        _ = editor(
            command="insert",
            path=str(test_file),
            insert_line=i,
            new_str=f"Line {i + 1}",
        )

    # View file
    result = editor(command="view", path=str(test_file))
    assert "     1\tLine 1" in result.text
    assert "   500\tLine 500" in result.text

    # Replace line 500's content with '500 new'
    result = editor(
        command="str_replace",
        path=str(test_file),
        old_str="Line 500",
        new_str="500 new",
    )
    assert "   500\t500 new" in result.text

    # Delete the line '500 new'
    result = editor(
        command="str_replace", path=str(test_file), old_str="500 new\n", new_str=""
    )
    assert "   499\tLine 499" in result.text
    assert "   500\tLine 501" in result.text

    # Insert content at line 500
    result = editor(
        command="insert",
        path=str(test_file),
        insert_line=499,
        new_str="Inserted line at 500",
    )
    assert "   500\tInserted line at 500" in result.text
