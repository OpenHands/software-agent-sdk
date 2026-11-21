"""Use a real read-only directory to test undo retry through the public executor."""

import json
import os
import tempfile
from pathlib import Path

from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.file_editor.impl import FileEditorExecutor

if os.geteuid() == 0:
    raise RuntimeError("Run this permission reproduction as an unprivileged user.")

with tempfile.TemporaryDirectory(prefix="openhands-undo-live-") as temporary:
    directory = Path(temporary)
    path = directory / "example.py"
    path.write_text("value = 0\n", encoding="utf-8")
    executor = FileEditorExecutor(workspace_root=temporary)
    for before, after in ((0, 1), (1, 2)):
        edited = executor(FileEditorAction(
            command="str_replace", path=str(path),
            old_str=f"value = {before}", new_str=f"value = {after}",
        ))
        assert not edited.is_error
    directory.chmod(0o555)
    try:
        failed = executor(FileEditorAction(command="undo_edit", path=str(path)))
        after_failure = path.read_text(encoding="utf-8")
    finally:
        directory.chmod(0o755)
    retried = executor(FileEditorAction(command="undo_edit", path=str(path)))
    after_retry = path.read_text(encoding="utf-8")
    older = executor(FileEditorAction(command="undo_edit", path=str(path)))
    after_older = path.read_text(encoding="utf-8")
    report = {
        "failure_was_reported": failed.is_error,
        "after_failed_undo": after_failure,
        "retry_is_error": retried.is_error,
        "after_retry": after_retry,
        "older_undo_is_error": older.is_error,
        "after_older_undo": after_older,
    }
    print(json.dumps(report), flush=True)
    assert failed.is_error and after_failure == "value = 2\n"
    assert not retried.is_error and after_retry == "value = 1\n"
    assert not older.is_error and after_older == "value = 0\n"
