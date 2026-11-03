"""Edge-case tests for the file editor."""

import pytest

from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.exceptions import ToolError


@pytest.fixture
def editor() -> FileEditor:
    return FileEditor()


def test_str_replace_rejects_overlapping_occurrences(editor, tmp_path):
    # "aa" occurs at offsets 0 and 1 of "aaa", so it does not identify a single
    # location and the replacement must be refused.
    path = tmp_path / "overlap.txt"
    path.write_text("aaa\n")

    with pytest.raises(ToolError, match="Multiple occurrences"):
        editor(
            command="str_replace",
            path=str(path),
            old_str="aa",
            new_str="b",
        )

    assert path.read_text() == "aaa\n"
