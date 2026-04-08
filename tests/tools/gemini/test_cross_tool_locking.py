"""Cross-tool test: Gemini tools and FileEditorTool must produce the same
resource key for the same file so that the parallel executor serializes
access correctly across tool boundaries.
"""

from pathlib import Path

from openhands.tools.file_editor.definition import FileEditorAction, FileEditorTool
from openhands.tools.gemini.edit.definition import EditAction, EditTool
from openhands.tools.gemini.read_file.definition import ReadFileAction, ReadFileTool
from openhands.tools.gemini.write_file.definition import WriteFileAction, WriteFileTool


def test_gemini_and_file_editor_produce_same_key(fake_conv_state):
    """A Gemini relative path and a FileEditorTool absolute path for the same
    file must yield identical resource keys."""
    workspace = fake_conv_state.workspace.working_dir
    abs_path = str(Path(workspace) / "src" / "foo.py")

    # Gemini tools with a relative path
    edit_tool = EditTool.create(conv_state=fake_conv_state)[0]
    read_tool = ReadFileTool.create(conv_state=fake_conv_state)[0]
    write_tool = WriteFileTool.create(conv_state=fake_conv_state)[0]

    gemini_edit_key = edit_tool.declared_resources(
        EditAction(file_path="src/foo.py", old_string="", new_string="x")
    ).keys[0]
    gemini_read_key = read_tool.declared_resources(
        ReadFileAction(file_path="src/foo.py")
    ).keys[0]
    gemini_write_key = write_tool.declared_resources(
        WriteFileAction(file_path="src/foo.py", content="x")
    ).keys[0]

    # FileEditorTool with an absolute path
    file_editor_tool = FileEditorTool.create(conv_state=fake_conv_state)[0]
    file_editor_key = file_editor_tool.declared_resources(
        FileEditorAction(command="view", path=abs_path)
    ).keys[0]

    # All must agree
    assert gemini_edit_key == file_editor_key
    assert gemini_read_key == file_editor_key
    assert gemini_write_key == file_editor_key
