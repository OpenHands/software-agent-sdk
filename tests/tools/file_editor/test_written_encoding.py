import os

import pytest

from openhands.tools.file_editor.editor import FileEditor


_CP1252_TEXT = (
    "“Don’t forget” — the café’s menu costs €12.\n"
    "Résumé: crème brûlée and jalapeños.\n"
    "Status: TOKEN\n"
)
_ACCENTED_LINE = "Résumé: crème brûlée and jalapeños."


@pytest.mark.parametrize("suffix", ["", "End of report.\n"], ids=["drift", "stable"])
def test_str_replace_keeps_cp1252_for_editor_reads(tmp_path, suffix):
    path = tmp_path / "notes.txt"
    original = _CP1252_TEXT + suffix
    path.write_bytes(original.encode("cp1252"))
    editor = FileEditor()
    assert _ACCENTED_LINE in editor(command="view", path=str(path)).text

    edit = editor(command="str_replace", path=str(path), old_str="TOKEN", new_str="X")
    expected = original.replace("TOKEN", "X")
    assert path.read_bytes() == expected.replace("\n", os.linesep).encode("cp1252")
    assert edit.new_content == expected
    view = editor(command="view", path=str(path))
    assert (_ACCENTED_LINE in edit.text, _ACCENTED_LINE in view.text) == (True, True)


def test_str_replace_remembers_utf8_after_fallback(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(_CP1252_TEXT.encode("cp1252"))
    editor = FileEditor()

    edit = editor(command="str_replace", path=str(path), old_str="TOKEN", new_str="→")

    expected = _CP1252_TEXT.replace("TOKEN", "→")
    assert path.read_bytes() == expected.replace("\n", os.linesep).encode("utf-8")
    assert _ACCENTED_LINE in edit.text
    assert _ACCENTED_LINE in editor(command="view", path=str(path)).text
