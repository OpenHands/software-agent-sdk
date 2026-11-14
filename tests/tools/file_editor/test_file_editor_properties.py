"""Property tests for FileEditor against an in-memory text model.

The file is modelled as a string plus an undo stack, with lines split the way
iterating a file splits them: only "\\n" ends a line. After any sequence of
insert, str_replace (with an exact or a whitespace-padded old_str) and
undo_edit, the file on disk equals the model, ``view`` lists the model's lines
numbered from 1, and ``view`` with ``view_range=[i, n]`` lists lines i..n under
the same numbers.
"""

import tempfile
from pathlib import Path

from hypothesis import settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from openhands.tools.file_editor.editor import FileEditor


# Form feed is whitespace and a str.splitlines() boundary, but not a line end.
FILE_TEXT = st.text(alphabet="ab \n\x0c", max_size=20)
INSERT_TEXT = st.text(alphabet="xy \n\x0c", max_size=8)
REPLACEMENT_TEXT = st.text(alphabet="xy \n", max_size=6)


def file_lines(text: str) -> list[str]:
    """Split ``text`` into lines that keep their "\\n" terminator."""
    *terminated, last = text.split("\n")
    return [line + "\n" for line in terminated] + ([last] if last else [])


def numbered_view(lines: list[str], start: int) -> list[str]:
    """The lines ``view`` prints after its header, ending with an empty one."""
    return [f"{number:6}\t{line}" for number, line in enumerate(lines, start)] + [""]


def occurs_once(text: str, sub: str) -> bool:
    return text.find(sub, text.find(sub) + 1) == -1


def unique_trimmed_substrings(text: str) -> list[str]:
    substrings = {
        text[i:j] for i in range(len(text)) for j in range(i + 1, len(text) + 1)
    }
    return sorted(
        (s for s in substrings if s == s.strip() and occurs_once(text, s)),
        key=lambda s: (len(s), s),
    )


class FileEditorMatchesTextModel(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.workdir = tempfile.TemporaryDirectory()
        self.path = Path(self.workdir.name) / "file.txt"
        self.editor = FileEditor(workspace_root=self.workdir.name)
        self.text = ""
        self.undo_stack: list[str] = []

    def teardown(self) -> None:
        self.workdir.cleanup()

    def _record_edit(self, new_text: str) -> None:
        self.undo_stack.append(self.text)
        self.text = new_text

    @initialize(text=FILE_TEXT)
    def write_file(self, text: str) -> None:
        self.path.write_text(text)
        self.text = text

    @rule(data=st.data(), new_str=INSERT_TEXT)
    def insert(self, data: st.DataObject, new_str: str) -> None:
        lines = file_lines(self.text)
        insert_line = data.draw(st.integers(0, len(lines)), label="insert_line")
        head = lines[:insert_line]
        if head and not head[-1].endswith("\n"):
            head[-1] += "\n"
        inserted = [line + "\n" for line in new_str.split("\n")]
        self.editor(
            command="insert",
            path=str(self.path),
            insert_line=insert_line,
            new_str=new_str,
        )
        self._record_edit("".join(head + inserted + lines[insert_line:]))

    @precondition(lambda self: self.text.strip())
    @rule(data=st.data(), pad=st.booleans())
    def str_replace(self, data: st.DataObject, pad: bool) -> None:
        old_str = data.draw(
            st.sampled_from(unique_trimmed_substrings(self.text)), label="old_str"
        )
        padded = f" {old_str} "
        sent_old_str = padded if pad and padded not in self.text else old_str
        new_str = data.draw(
            REPLACEMENT_TEXT.filter(lambda s: s != sent_old_str), label="new_str"
        )
        self.editor(
            command="str_replace",
            path=str(self.path),
            old_str=sent_old_str,
            new_str=new_str,
        )
        self._record_edit(self.text.replace(old_str, new_str, 1))

    @precondition(lambda self: self.undo_stack)
    @rule()
    def undo_edit(self) -> None:
        self.editor(command="undo_edit", path=str(self.path))
        self.text = self.undo_stack.pop()

    @invariant()
    def file_and_views_match_model(self) -> None:
        assert self.path.read_text() == self.text
        lines = [line.removesuffix("\n") for line in file_lines(self.text)]
        full = self.editor(command="view", path=str(self.path))
        assert full.text.split("\n")[1:] == numbered_view(lines, 1)
        for start in range(1, len(lines) + 1):
            ranged = self.editor(
                command="view", path=str(self.path), view_range=[start, len(lines)]
            )
            assert ranged.text.split("\n")[1:] == numbered_view(
                lines[start - 1 :], start
            ), f"view_range=[{start}, {len(lines)}]"


def test_file_editor_matches_text_model() -> None:
    # Fewer steps than the editor keeps undo entries, so the model needs no cap.
    run_state_machine_as_test(
        FileEditorMatchesTextModel, settings=settings(stateful_step_count=8)
    )
