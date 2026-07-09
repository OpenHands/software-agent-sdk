"""``affected_paths`` on tool observations drives path-rule injection.

Each file-touching observation reports the path(s) it modified so
``LocalConversation`` can fire path rules without guessing a ``path`` field on
the action. Search/listing observations touch no files and report nothing,
which keeps rules from firing spuriously on a directory that was only searched.
"""

from openhands.tools.apply_patch.core import ActionType, Commit, FileChange
from openhands.tools.apply_patch.definition import ApplyPatchObservation
from openhands.tools.file_editor import FileEditorObservation
from openhands.tools.gemini import (
    EditObservation,
    ReadFileObservation,
    WriteFileObservation,
)
from openhands.tools.glob import GlobObservation
from openhands.tools.grep import GrepObservation


def test_file_editor_reports_edited_path() -> None:
    obs = FileEditorObservation(command="str_replace", path="/w/a.py")
    assert obs.affected_paths == ["/w/a.py"]


def test_file_editor_reports_nothing_on_error() -> None:
    obs = FileEditorObservation(command="str_replace", path="/w/a.py", is_error=True)
    assert obs.affected_paths == []


def test_gemini_write_edit_read_report_file_path() -> None:
    assert WriteFileObservation(file_path="/w/a.py").affected_paths == ["/w/a.py"]
    assert EditObservation(file_path="/w/b.py").affected_paths == ["/w/b.py"]
    assert ReadFileObservation(file_path="/w/c.py").affected_paths == ["/w/c.py"]


def test_apply_patch_reports_all_changed_and_moved_paths() -> None:
    commit = Commit(
        changes={
            "src/api/a.ts": FileChange(type=ActionType.UPDATE),
            "src/api/b.ts": FileChange(type=ActionType.ADD),
            "old.ts": FileChange(type=ActionType.UPDATE, move_path="src/api/new.ts"),
        }
    )
    obs = ApplyPatchObservation(commit=commit)
    # Both the changed files and the rename destination are reported.
    assert obs.affected_paths == [
        "src/api/a.ts",
        "src/api/b.ts",
        "old.ts",
        "src/api/new.ts",
    ]


def test_search_observations_report_no_paths() -> None:
    # grep/glob only search a directory; they modify nothing, so path rules
    # must not fire even though the observations carry file lists.
    assert (
        GrepObservation(matches=[], pattern="x", search_path="/w").affected_paths == []
    )
    assert GlobObservation(files=[], pattern="*", search_path="/w").affected_paths == []
