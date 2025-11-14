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
