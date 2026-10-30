"""Edge-case and failure-path tests for the file editor."""

import codecs
import os
from pathlib import Path

import pytest

from openhands.tools.file_editor import file_editor
from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.exceptions import (
    EditorToolParameterInvalidError,
    ToolError,
)
from tests.platform_utils import symlink_or_skip


@pytest.fixture
def editor() -> FileEditor:
    return FileEditor()


def test_str_replace_treats_regex_metacharacters_literally(editor, tmp_path):
    path = tmp_path / "re.txt"
    path.write_text("x = a.b*c(d)\nx = aXbbc(d)\n")

    editor(
        command="str_replace",
        path=str(path),
        old_str="a.b*c(d)",
        new_str=r"\1 $& \g<0>",
    )

    assert path.read_text() == "x = \\1 $& \\g<0>\nx = aXbbc(d)\n"


def test_str_replace_reports_unicode_duplicate_lines(editor, tmp_path):
    path = tmp_path / "uni.txt"
    path.write_text("café 😀\nplain\ncafé 😀\n", encoding="utf-8")

    with pytest.raises(ToolError) as exc_info:
        editor(command="str_replace", path=str(path), old_str="café 😀", new_str="x")

    assert "[1, 3]" in exc_info.value.message
    assert path.read_text(encoding="utf-8") == "café 😀\nplain\ncafé 😀\n"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: str_replace uniqueness check misses overlapping occurrences",
)
def test_str_replace_rejects_overlapping_occurrences(editor, tmp_path):
    # "aa" occurs at offsets 0 and 1 of "aaa", so it does not identify a single
    # location and the replacement must be refused.
    path = tmp_path / "overlap.txt"
    path.write_text("aaa\n")

    with pytest.raises(ToolError, match="Multiple occurrences"):
        editor(command="str_replace", path=str(path), old_str="aa", new_str="b")

    assert path.read_text() == "aaa\n"


def test_str_replace_keeps_utf8_bom(editor, tmp_path):
    path = tmp_path / "bom.txt"
    path.write_bytes(codecs.BOM_UTF8 + b"hello\n")

    editor(command="str_replace", path=str(path), old_str="hello", new_str="bye")

    assert path.read_bytes() == codecs.BOM_UTF8 + b"bye\n"


def test_str_replace_keeps_latin1_encoding(editor, tmp_path):
    path = tmp_path / "latin1.txt"
    original = "café au lait, naïve résumé à la carte\n"
    path.write_bytes(original.encode("latin-1"))

    editor(command="str_replace", path=str(path), old_str="lait", new_str="LAIT")

    assert path.read_bytes() == original.replace("lait", "LAIT").encode("latin-1")


@pytest.mark.skipif(
    os.name == "nt", reason="text-mode writes emit CRLF natively on Windows"
)
@pytest.mark.xfail(
    strict=True,
    reason="BUG: edits rewrite CRLF line endings as LF for the whole file",
)
def test_str_replace_preserves_crlf_line_endings(editor, tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")

    editor(command="str_replace", path=str(path), old_str="two", new_str="TWO")

    assert path.read_bytes() == b"one\r\nTWO\r\nthree\r\n"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: atomic write replaces a symlink with a regular file",
)
def test_str_replace_through_symlink_edits_target(editor, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("hello\n")
    link = tmp_path / "link.txt"
    symlink_or_skip(target, link)

    editor(command="str_replace", path=str(link), old_str="hello", new_str="bye")

    assert link.is_symlink()
    assert target.read_text() == "bye\n"


def test_create_in_missing_parent_dir_errors_without_leftovers(tmp_path):
    path = tmp_path / "missing" / "deep" / "new.txt"

    result = file_editor(command="create", path=str(path), file_text="x")

    assert result.is_error
    assert not path.exists()
    assert not (tmp_path / "missing").exists()


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions and a non-root user",
)
@pytest.mark.xfail(
    strict=True,
    reason="BUG: unreadable file raises PermissionError instead of a tool error",
)
def test_view_unreadable_file_returns_error_observation(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("secret\n")
    os.chmod(path, 0)
    try:
        result = file_editor(command="view", path=str(path))
    finally:
        os.chmod(path, 0o644)

    assert result.is_error
    assert "secret.txt" in result.text


def test_view_range_end_before_start_is_rejected(editor, tmp_path):
    path = tmp_path / "v.txt"
    path.write_text("1\n2\n3\n")

    with pytest.raises(EditorToolParameterInvalidError, match="second element"):
        editor(command="view", path=str(path), view_range=[3, 2])


def test_view_range_negative_end_other_than_minus_one_is_rejected(editor, tmp_path):
    path = tmp_path / "v.txt"
    path.write_text("1\n2\n3\n")

    with pytest.raises(EditorToolParameterInvalidError):
        editor(command="view", path=str(path), view_range=[1, -2])


def test_view_range_single_last_line(editor, tmp_path):
    path = tmp_path / "v.txt"
    path.write_text("1\n2\nlast")

    result = editor(command="view", path=str(path), view_range=[3, 3])

    assert "     3\tlast" in result.text
    assert "     2\t" not in result.text


def test_view_range_on_empty_file_is_rejected(editor, tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("")

    with pytest.raises(EditorToolParameterInvalidError, match="first element"):
        editor(command="view", path=str(path), view_range=[1, -1])


def test_insert_into_empty_file(editor, tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("")

    editor(command="insert", path=str(path), insert_line=0, new_str="hi")

    assert path.read_text() == "hi\n"


def test_insert_negative_line_is_rejected(editor, tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("a\nb\n")

    with pytest.raises(EditorToolParameterInvalidError):
        editor(command="insert", path=str(path), insert_line=-1, new_str="x")

    assert path.read_text() == "a\nb\n"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: insert after a last line with no trailing newline merges lines",
)
def test_insert_after_last_line_without_trailing_newline(editor, tmp_path):
    path = tmp_path / "no_eol.txt"
    path.write_text("a\nb")

    editor(command="insert", path=str(path), insert_line=2, new_str="x")

    lines = path.read_text().splitlines()
    assert lines == ["a", "b", "x"]


def test_undo_restores_original_after_failed_replace(editor, tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("alpha\nbeta\n")
    editor(command="str_replace", path=str(path), old_str="alpha", new_str="ALPHA")

    with pytest.raises(ToolError):
        editor(command="str_replace", path=str(path), old_str="gamma", new_str="x")
    editor(command="undo_edit", path=str(path))

    assert path.read_text() == "alpha\nbeta\n"
    with pytest.raises(ToolError, match="No edit history"):
        editor(command="undo_edit", path=str(path))


def test_edit_leaves_no_temp_files(editor, tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("alpha\n")

    editor(command="str_replace", path=str(path), old_str="alpha", new_str="beta")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_binary_file_cannot_be_edited(editor, tmp_path):
    path = tmp_path / "blob.bin"
    data = bytes(range(256)) * 4
    path.write_bytes(data)

    with pytest.raises(ToolError):
        editor(command="str_replace", path=str(path), old_str="a", new_str="b")

    assert Path(path).read_bytes() == data
