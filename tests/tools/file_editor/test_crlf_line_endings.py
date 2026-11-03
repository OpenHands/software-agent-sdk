"""Edge-case tests for the file editor commands.

One test per bug pinned in OpenHands/software-agent-sdk#6348.
"""

from openhands.tools.file_editor.editor import FileEditor


def test_str_replace_preserves_crlf_line_endings(editor: FileEditor, tmp_path):
    """An edit must not rewrite the endings of the lines it did not touch."""
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")

    editor(command="str_replace", path=str(path), old_str="two", new_str="TWO")

    assert path.read_bytes() == b"one\r\nTWO\r\nthree\r\n"
