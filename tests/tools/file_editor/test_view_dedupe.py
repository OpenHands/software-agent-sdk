"""SDK-6: file_editor returns a small dedupe hint when the same `view` is
re-requested without intervening edits.

Motivation: trajectory analysis of run `27224350936` (Nemotron 550B on
SWE-Bench Verified) showed the three expensive instances re-viewing the same
unchanged file repeatedly — `_iterative.py` 34 times, `schema.py` 19 times,
`base.py` 6 times. Each repeat re-emitted the full file content into the
conversation, which is then paid for on every subsequent uncached turn.

This module pins:
* Repeat view of unchanged file → short hint.
* Repeat view AFTER an edit → fresh content.
* View with different `view_range` → fresh content (different bytes returned).
* Multiple distinct paths tracked independently.
* Hint payload is genuinely small (well under any reasonable view of the file).
* `is_error` views never get deduped.
"""

from pathlib import Path

from openhands.tools.file_editor.editor import FileEditor


_DEDUPE_MARKER = "[file_editor view dedupe]"


def _view(editor: FileEditor, path: Path, view_range: list[int] | None = None):
    return editor(command="view", path=str(path), view_range=view_range)


# --------------------------------------------------------------------------
# Core behaviour
# --------------------------------------------------------------------------


def test_repeat_view_unchanged_file_returns_hint(tmp_path: Path) -> None:
    editor = FileEditor()
    f = tmp_path / "schema.py"
    f.write_text("class Schema:\n    pass\n")

    first = _view(editor, f)
    second = _view(editor, f)

    assert _DEDUPE_MARKER not in first.text
    assert _DEDUPE_MARKER in second.text
    # The hint should name the file so the model can map it back.
    assert str(f) in second.text
    # And teach the recovery paths: scroll back, or use `view_range`/`cat`.
    assert "view_range" in second.text
    assert "scroll back" in second.text.lower() or "previous" in second.text.lower()


def test_hint_is_much_smaller_than_real_view(tmp_path: Path) -> None:
    """The whole point of dedupe is token savings. On a representative
    real-world file (the kind agents actually loop over — e.g. Django
    `schema.py` ~1500 lines), the hint should be < 5% of the original.
    Use 1000 lines here as a conservative proxy."""
    editor = FileEditor()
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"line_{i} = {i}" for i in range(1000)) + "\n")

    first = _view(editor, f)
    second = _view(editor, f)

    ratio = len(second.text) / len(first.text)
    assert ratio < 0.05, (
        f"Dedupe hint ({len(second.text)} chars) should be < 5% of the "
        f"original view ({len(first.text)} chars); got ratio={ratio:.2%}"
    )


# --------------------------------------------------------------------------
# Invalidation semantics
# --------------------------------------------------------------------------


def test_edit_invalidates_dedupe(tmp_path: Path) -> None:
    editor = FileEditor()
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    first = _view(editor, f)
    assert _DEDUPE_MARKER not in first.text

    editor(command="str_replace", path=str(f), old_str="x = 1", new_str="x = 2")

    third = _view(editor, f)
    assert _DEDUPE_MARKER not in third.text, (
        "After an edit, the next view must return real content, not a hint"
    )
    assert "x = 2" in third.text


def test_create_invalidates_dedupe(tmp_path: Path) -> None:
    """`create` is a write — must clear cached view of that path so a
    subsequent view sees the new content rather than the old hint.

    `create` refuses to overwrite existing files, so we view the file once,
    then delete + re-create it, then view again."""
    editor = FileEditor()
    f = tmp_path / "fresh.py"
    f.write_text("v1\n")

    _view(editor, f)
    f.unlink()
    editor(command="create", path=str(f), file_text="v2\n")
    third = _view(editor, f)

    assert _DEDUPE_MARKER not in third.text
    assert "v2" in third.text


def test_insert_invalidates_dedupe(tmp_path: Path) -> None:
    editor = FileEditor()
    f = tmp_path / "ins.py"
    f.write_text("line0\n")

    _view(editor, f)
    editor(command="insert", path=str(f), insert_line=1, new_str="line1")
    third = _view(editor, f)

    assert _DEDUPE_MARKER not in third.text
    assert "line1" in third.text


def test_undo_edit_invalidates_dedupe(tmp_path: Path) -> None:
    editor = FileEditor()
    f = tmp_path / "u.py"
    f.write_text("v1\n")

    _view(editor, f)
    editor(command="str_replace", path=str(f), old_str="v1", new_str="v2")
    # `undo_edit` rewrites the file → write_file → cache clears.
    editor(command="undo_edit", path=str(f))
    after_undo = _view(editor, f)

    assert _DEDUPE_MARKER not in after_undo.text
    assert "v1" in after_undo.text


# --------------------------------------------------------------------------
# Range / multi-file semantics
# --------------------------------------------------------------------------


def test_different_view_range_is_not_deduped(tmp_path: Path) -> None:
    """A new `view_range` produces different bytes — it must NOT dedupe.
    The agent might genuinely want lines it hasn't seen yet."""
    editor = FileEditor()
    f = tmp_path / "r.py"
    f.write_text("\n".join(f"line{i}" for i in range(20)) + "\n")

    a = _view(editor, f, view_range=[1, 5])
    b = _view(editor, f, view_range=[10, 15])

    assert _DEDUPE_MARKER not in a.text
    assert _DEDUPE_MARKER not in b.text


def test_aba_view_pattern_still_dedupes(tmp_path: Path) -> None:
    """View range A, then range B, then range A again — the third call
    repeats the first response so it must dedupe. This is the realistic
    pattern: the model looks at one slice, jumps to another for context,
    then circles back. Per-path caching (rather than per-(path,range)
    overwrite) makes this work."""
    editor = FileEditor()
    f = tmp_path / "r.py"
    f.write_text("\n".join(f"line{i}" for i in range(20)) + "\n")

    _view(editor, f, view_range=[1, 5])
    _view(editor, f, view_range=[10, 15])
    c = _view(editor, f, view_range=[1, 5])

    assert _DEDUPE_MARKER in c.text


def test_distinct_paths_are_tracked_independently(tmp_path: Path) -> None:
    editor = FileEditor()
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a content\n")
    b.write_text("b content\n")

    _view(editor, a)
    fresh_b = _view(editor, b)  # First view of b — must not be deduped.
    assert _DEDUPE_MARKER not in fresh_b.text


def test_repeat_directory_view_is_deduped(tmp_path: Path) -> None:
    """Directory listings of an unchanged directory should also dedupe — they
    can be large for big trees and the model often re-`ls`'s the same dir."""
    editor = FileEditor()
    (tmp_path / "child.py").write_text("x\n")

    first = _view(editor, tmp_path)
    second = _view(editor, tmp_path)

    assert _DEDUPE_MARKER not in first.text
    assert _DEDUPE_MARKER in second.text


# --------------------------------------------------------------------------
# Error / safety
# --------------------------------------------------------------------------


def test_error_observations_are_never_deduped(tmp_path: Path) -> None:
    """If the first view errored (e.g. nonexistent file), the model must see
    the full error every time; deduping errors would silently mask retries."""
    editor = FileEditor()
    missing = tmp_path / "nope.py"

    # Both calls should raise (they don't even reach the dedupe path), but
    # to be safe we also ensure the dedupe cache isn't populated on error.
    for _ in range(2):
        try:
            _view(editor, missing)
        except Exception:
            pass

    # After the (failed) views, a real file at the same name must view fully.
    missing.write_text("hello\n")
    obs = _view(editor, missing)
    assert _DEDUPE_MARKER not in obs.text
    assert "hello" in obs.text
