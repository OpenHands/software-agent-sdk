"""Model-based property tests for the apply_patch parser.

Each test picks a file and an edit, writes the patch the way a model would
(up to three lines of context around ``-`` and ``+`` lines), applies it, and
compares the result with the same edit done on a plain list of lines.
"""

import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, example, given, strategies as st

from openhands.tools.apply_patch.core import (
    ActionType,
    patch_to_commit,
    text_to_patch,
)
from openhands.tools.apply_patch.definition import (
    ApplyPatchAction,
    ApplyPatchExecutor,
)


# A small alphabet so that repeated lines, blank lines and lines differing
# only in whitespace are common.
line = st.sampled_from(["", "a", "b", "a ", " a", "\ta", "x = 1", "}", "@@", "***"])
file_lines = st.lists(line, min_size=1, max_size=12)

PATH = "f.txt"


def _occurrences(lines: list[str], block: list[str]) -> int:
    return sum(
        lines[i : i + len(block)] == block for i in range(len(lines) - len(block) + 1)
    )


def _apply(patch_text: str, files: dict[str, str]):
    patch, _ = text_to_patch(patch_text, files)
    return patch_to_commit(patch, files)


@st.composite
def single_edit(draw):
    lines = draw(file_lines)
    i = draw(st.integers(0, len(lines)))
    j = draw(st.integers(i, len(lines)))
    new = draw(st.lists(line, max_size=4))
    before, after = lines[max(0, i - 3) : i], lines[j : j + 3]
    return lines, i, j, new, before, after


@given(single_edit())
def test_update_applies_an_unambiguous_edit(case):
    lines, i, j, new, before, after = case
    old_block = before + lines[i:j] + after
    assume(old_block and _occurrences(lines, old_block) == 1)
    assume(lines[i:j] != new)

    body = (
        [" " + s for s in before]
        + ["-" + s for s in lines[i:j]]
        + ["+" + s for s in new]
        + [" " + s for s in after]
    )
    patch_text = "\n".join(
        ["*** Begin Patch", f"*** Update File: {PATH}", "@@", *body, "*** End Patch"]
    )
    commit = _apply(patch_text, {PATH: "\n".join(lines)})
    assert commit.changes[PATH].new_content == "\n".join(lines[:i] + new + lines[j:])


def _section(lines, i, j, new):
    before, after = lines[max(0, i - 3) : i], lines[j : j + 3]
    body = (
        [" " + s for s in before]
        + ["-" + s for s in lines[i:j]]
        + ["+" + s for s in new]
        + [" " + s for s in after]
    )
    start = max(0, i - 3)
    return ["@@", *body], start, start + len(before) + (j - i) + len(after)


# Varied enough that two hunks' context blocks are almost always unique.
varied_line = line | st.text("ab x=}", min_size=1, max_size=4)


@given(st.lists(varied_line, min_size=8, max_size=24), st.data())
def test_update_applies_two_unambiguous_edits(lines, data):
    i1 = data.draw(st.integers(0, len(lines)))
    j1 = data.draw(st.integers(i1, len(lines)))
    # Leave room for both hunks' context so they rarely overlap.
    i2 = data.draw(st.integers(min(j1 + 6, len(lines)), len(lines)))
    j2 = data.draw(st.integers(i2, len(lines)))
    new1 = data.draw(st.lists(varied_line, max_size=3))
    new2 = data.draw(st.lists(varied_line, max_size=3))
    assume(lines[i1:j1] != new1 and lines[i2:j2] != new2)

    section1, start1, end1 = _section(lines, i1, j1, new1)
    section2, start2, end2 = _section(lines, i2, j2, new2)
    # Separate hunks whose old blocks do not overlap and occur exactly once.
    assume(end1 <= start2)
    for start, end in ((start1, end1), (start2, end2)):
        assume(end > start and _occurrences(lines, lines[start:end]) == 1)

    patch_text = "\n".join(
        ["*** Begin Patch", f"*** Update File: {PATH}", *section1, *section2]
        + ["*** End Patch"]
    )
    commit = _apply(patch_text, {PATH: "\n".join(lines)})
    expected = lines[:i1] + new1 + lines[j1:i2] + new2 + lines[j2:]
    assert commit.changes[PATH].new_content == "\n".join(expected)


@pytest.mark.xfail(
    strict=True, reason="updating a CRLF file rewrites its line endings (#6348)"
)
@given(single_edit(), st.sampled_from(["\n", "\r\n"]))
@example(([""], 0, 0, [""], [], [""]), "\r\n")
def test_executor_edit_keeps_the_file_line_endings(case, newline):
    lines, i, j, new, before, after = case
    old_block = before + lines[i:j] + after
    assume(old_block and _occurrences(lines, old_block) == 1)
    assume(lines[i:j] != new)
    body = (
        [" " + s for s in before]
        + ["-" + s for s in lines[i:j]]
        + ["+" + s for s in new]
        + [" " + s for s in after]
    )
    patch_text = "\n".join(
        ["*** Begin Patch", f"*** Update File: {PATH}", "@@", *body, "*** End Patch"]
    )
    with tempfile.TemporaryDirectory() as workspace:
        target = Path(workspace) / PATH
        target.write_bytes(newline.join(lines).encode())
        executor = ApplyPatchExecutor(workspace_root=workspace)
        observation = executor(ApplyPatchAction(patch=patch_text))
        assert not observation.is_error, observation.text
        expected = newline.join(lines[:i] + new + lines[j:])
        assert target.read_bytes() == expected.encode()


@given(st.lists(line, min_size=1, max_size=12))
def test_add_file_writes_exactly_the_added_lines(lines):
    patch_text = "\n".join(
        ["*** Begin Patch", f"*** Add File: {PATH}", *["+" + s for s in lines]]
        + ["*** End Patch"]
    )
    commit = _apply(patch_text, {})
    assert commit.changes[PATH].type == ActionType.ADD
    assert commit.changes[PATH].new_content == "\n".join(lines)
