import os
from pathlib import Path

import pytest

from openhands.tools.apply_patch.definition import ApplyPatchAction, ApplyPatchExecutor


@pytest.fixture()
def tmp_ws(tmp_path: Path) -> Path:
    # create a temp workspace root
    return tmp_path


def run_exec(ws: Path, patch: str):
    ex = ApplyPatchExecutor(workspace_root=str(ws))
    return ex(ApplyPatchAction(patch=patch))


def test_create_modify_delete(tmp_ws: Path):
    # 1) create FACTS.txt
    patch1 = (
        "*** Begin Patch\n"
        "*** Add File: FACTS.txt\n"
        "+OpenHands SDK integrates tools.\n"
        "*** End Patch"
    )
    obs1 = run_exec(tmp_ws, patch1)
    assert not obs1.is_error
    fp = tmp_ws / "FACTS.txt"
    assert fp.exists()
    assert fp.read_text().rstrip("\n") == "OpenHands SDK integrates tools."

    # 2) append a second line
    patch2 = (
        "*** Begin Patch\n"
        "*** Update File: FACTS.txt\n"
        "@@\n"
        " OpenHands SDK integrates tools.\n"
        "+ApplyPatch works.\n"
        "*** End Patch"
    )
    obs2 = run_exec(tmp_ws, patch2)
    assert not obs2.is_error
    assert fp.read_text() == ("OpenHands SDK integrates tools.\nApplyPatch works.")

    # 3) delete
    patch3 = "*** Begin Patch\n*** Delete File: FACTS.txt\n*** End Patch"
    obs3 = run_exec(tmp_ws, patch3)
    assert not obs3.is_error
    assert not fp.exists()


def test_reject_absolute_path(tmp_ws: Path):
    # refuse escape/absolute paths
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {os.path.abspath('/etc/passwd')}\n"
        "+x\n"
        "*** End Patch"
    )
    obs = run_exec(tmp_ws, patch)
    assert obs.is_error
    assert "Absolute or escaping paths" in obs.text


def test_multi_hunk_invalid_context_error(tmp_ws: Path):
    fp = tmp_ws / "multi.txt"
    fp.write_text("line1\nline2\nline3\nline4\n")

    patch = (
        "*** Begin Patch\n"
        "*** Update File: multi.txt\n"
        "@@\n"
        " line1\n"
        "-line2\n"
        "+line2a\n"
        " line3\n"
        "@@\n"
        " line3\n"
        "+line3a\n"
        " line4\n"
        "*** End Patch"
    )

    obs = run_exec(tmp_ws, patch)
    assert obs.is_error
    assert "Invalid Context" in obs.text


def test_fuzz_matching_trailing_spaces(tmp_ws: Path):
    fp = tmp_ws / "fuzz.txt"
    fp.write_text("a\ncontext line   \nend\n")

    patch = (
        "*** Begin Patch\n"
        "*** Update File: fuzz.txt\n"
        "@@\n"
        " context line\n"
        "-end\n"
        "+END\n"
        "*** End Patch"
    )

    obs = run_exec(tmp_ws, patch)
    assert not obs.is_error
    # fuzz should be > 0 because whitespace-stripped context is used
    assert obs.fuzz > 0
    assert fp.read_text() == "a\ncontext line   \nEND\n"


def test_duplicate_add_file_error(tmp_ws: Path):
    patch = (
        "*** Begin Patch\n"
        "*** Add File: dup.txt\n"
        "+one\n"
        "*** Add File: dup.txt\n"
        "+two\n"
        "*** End Patch"
    )
    obs = run_exec(tmp_ws, patch)
    assert obs.is_error
    assert "Add File Error: Duplicate Path" in obs.text


def test_path_escape_with_parent_directory(tmp_ws: Path):
    patch = "*** Begin Patch\n*** Add File: ../escape.txt\n+x\n*** End Patch"
    obs = run_exec(tmp_ws, patch)
    assert obs.is_error
    assert "Absolute or escaping paths" in obs.text
