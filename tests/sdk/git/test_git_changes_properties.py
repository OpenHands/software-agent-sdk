"""Property tests for git changes and diffs against a model of the working tree.

The model is the committed files as a name-to-text dict, plus the edits made to
the working tree since: modified or deleted committed files, and new untracked
ones. Against ``ref="HEAD"``, ``get_git_changes`` lists exactly the changed
names, verbatim (spaces and non-ASCII included), each once with its status; and
``get_git_diff`` of any committed or new name returns the committed text and the
working text ("" for a side the file is missing from), each normalised the way
``str.splitlines`` splits it.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import GitChangeStatus


FILE_NAMES = st.text(alphabet="ab é_-.", min_size=1, max_size=6).filter(
    lambda name: not name.startswith((".", "-"))
)
FILE_TEXT = st.text(alphabet="x \n", max_size=6)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def normalised(text: str) -> str:
    return "\n".join(text.splitlines())


# Each example runs several real git commands, so keep the example count low.
@settings(max_examples=25)
@given(data=st.data())
def test_git_changes_and_diffs_match_working_tree_model(data: st.DataObject) -> None:
    committed = data.draw(
        st.dictionaries(FILE_NAMES, FILE_TEXT, min_size=1, max_size=4),
        label="committed",
    )
    edits = data.draw(
        st.dictionaries(st.sampled_from(sorted(committed)), st.none() | FILE_TEXT),
        label="edits (None deletes)",
    )
    untracked = data.draw(
        st.dictionaries(
            FILE_NAMES.filter(lambda name: name not in committed),
            FILE_TEXT,
            max_size=3,
        ),
        label="untracked",
    )

    with tempfile.TemporaryDirectory() as workdir:
        repo = Path(workdir)
        git(repo, "init", "-q")
        for name, text in committed.items():
            (repo / name).write_text(text)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")

        working = dict(committed)
        for name, text in edits.items():
            if text is None:
                (repo / name).unlink()
                del working[name]
            else:
                (repo / name).write_text(text)
                working[name] = text
        for name, text in untracked.items():
            (repo / name).write_text(text)
            working[name] = text

        expected = sorted(
            [
                (name, GitChangeStatus.DELETED.value)
                for name in committed.keys() - working
            ]
            + [
                (name, GitChangeStatus.UPDATED.value)
                for name in committed.keys() & working.keys()
                if committed[name] != working[name]
            ]
            + [(name, GitChangeStatus.ADDED.value) for name in untracked]
        )
        changes = get_git_changes(repo, ref="HEAD")
        assert sorted((str(c.path), c.status.value) for c in changes) == expected

        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(repo)
            for name in sorted(committed.keys() | working.keys()):
                diff = get_git_diff(name, ref="HEAD")
                assert diff.original == normalised(committed.get(name, "")), name
                assert diff.modified == normalised(working.get(name, "")), name
