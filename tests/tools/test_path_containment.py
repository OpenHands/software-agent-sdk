"""Tests for opt-in workspace containment of file tools."""

import os
from pathlib import Path

import pytest

from openhands.tools.apply_patch.definition import ApplyPatchAction, ApplyPatchExecutor
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.file_editor.impl import FileEditorExecutor
from openhands.tools.gemini.edit.definition import EditAction
from openhands.tools.gemini.edit.impl import EditExecutor
from openhands.tools.gemini.list_directory.definition import ListDirectoryAction
from openhands.tools.gemini.list_directory.impl import ListDirectoryExecutor
from openhands.tools.gemini.read_file.definition import ReadFileAction
from openhands.tools.gemini.read_file.impl import ReadFileExecutor
from openhands.tools.gemini.write_file.definition import WriteFileAction
from openhands.tools.gemini.write_file.impl import WriteFileExecutor
from openhands.tools.glob.definition import GlobAction
from openhands.tools.glob.impl import GlobExecutor
from openhands.tools.grep.definition import GrepAction
from openhands.tools.grep.impl import GrepExecutor
from openhands.tools.planning_file_editor.definition import PlanningFileEditorAction
from openhands.tools.planning_file_editor.impl import PlanningFileEditorExecutor
from openhands.tools.utils.path_containment import (
    PathContainment,
    PathOutsideWorkspaceError,
    is_within_roots,
)


CANARY = "CANARY-OUTSIDE"


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    ws = tmp_path / "ws"
    sibling = tmp_path / "ws2"  # shares the "ws" prefix but is outside
    ws.mkdir()
    sibling.mkdir()
    (ws / "inside.txt").write_text("inside\n")
    secret = sibling / "secret.txt"
    secret.write_text(f"{CANARY}\nline2\n")
    return {"ws": ws, "sibling": sibling, "secret": secret}


# --- helper -----------------------------------------------------------------


def test_from_settings_off_returns_none(layout):
    assert PathContainment.from_settings(layout["ws"], False) is None


def test_allowed_roots_require_restriction(layout):
    with pytest.raises(ValueError, match="requires restrict_to_workspace"):
        PathContainment.from_settings(layout["ws"], False, [str(layout["sibling"])])


def test_allowed_roots_must_be_absolute(layout):
    with pytest.raises(ValueError, match="absolute"):
        PathContainment.create(layout["ws"], ["relative/dir"])


@pytest.mark.parametrize(
    "rel",
    [
        "inside.txt",
        "./inside.txt",
        "sub/../inside.txt",
        "new_dir/new_file.txt",  # nonexistent target, e.g. for create
    ],
)
def test_check_allows_paths_inside(layout, rel):
    c = PathContainment.create(layout["ws"])
    assert c.check(layout["ws"] / rel) == (layout["ws"] / rel).resolve()


def test_check_allows_root_itself_with_trailing_slash(layout):
    c = PathContainment.create(layout["ws"])
    assert c.check(str(layout["ws"]) + "/") == layout["ws"].resolve()
    assert c.check(str(layout["ws"]) + "//inside.txt")


@pytest.mark.parametrize(
    "make_path",
    [
        lambda L: L["secret"],  # absolute outside
        lambda L: L["ws"] / ".." / "ws2" / "secret.txt",  # ../ traversal
        lambda L: L["sibling"],  # shared prefix /x/ws vs /x/ws2
        lambda L: Path("/"),
    ],
)
def test_check_rejects_paths_outside(layout, make_path):
    c = PathContainment.create(layout["ws"])
    with pytest.raises(PathOutsideWorkspaceError, match="outside the workspace"):
        c.check(make_path(layout))


def test_relative_traversal_resolved_against_root(layout):
    c = PathContainment.create(layout["ws"])
    with pytest.raises(PathOutsideWorkspaceError, match="resolves to"):
        c.check("../ws2/secret.txt")


def test_symlinked_file_escape_rejected(layout):
    link = layout["ws"] / "link.txt"
    link.symlink_to(layout["secret"])
    with pytest.raises(PathOutsideWorkspaceError):
        PathContainment.create(layout["ws"]).check(link)


def test_symlinked_dir_escape_rejected(layout):
    link = layout["ws"] / "linkdir"
    link.symlink_to(layout["sibling"], target_is_directory=True)
    with pytest.raises(PathOutsideWorkspaceError):
        PathContainment.create(layout["ws"]).check(link / "secret.txt")


def test_symlink_inside_pointing_inside_allowed(layout):
    link = layout["ws"] / "alias.txt"
    link.symlink_to(layout["ws"] / "inside.txt")
    assert PathContainment.create(layout["ws"]).check(link)


def test_symlink_loop_does_not_escape_or_crash(layout):
    a, b = layout["ws"] / "a", layout["ws"] / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    c = PathContainment.create(layout["ws"])
    # Either rejected as unresolvable or kept inside the root; never outside.
    try:
        resolved = c.check(a)
    except PathOutsideWorkspaceError:
        return
    assert is_within_roots(resolved, c.roots)


def test_workspace_root_given_via_symlink(layout, tmp_path):
    root_link = tmp_path / "ws_link"
    root_link.symlink_to(layout["ws"], target_is_directory=True)
    c = PathContainment.create(root_link)
    assert c.check(layout["ws"] / "inside.txt")
    assert c.check(root_link / "inside.txt")


def test_extra_allowed_roots(layout):
    c = PathContainment.create(layout["ws"], [str(layout["sibling"])])
    assert c.check(layout["secret"]) == layout["secret"].resolve()


def test_case_is_compared_with_platform_rules(layout):
    upper = Path(str(layout["ws"]).upper()) / "inside.txt"
    c = PathContainment.create(layout["ws"])
    expected_inside = os.path.normcase("A") == os.path.normcase("a")
    if expected_inside:
        assert c.check(upper)
    else:
        with pytest.raises(PathOutsideWorkspaceError):
            c.check(upper)


# --- tools: restricted --------------------------------------------------------


def _err_text(obs) -> str:
    assert obs.is_error, obs
    return obs.text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"command": "view"},
        {"command": "str_replace", "old_str": "line2", "new_str": "X"},
        {"command": "insert", "insert_line": 0, "new_str": "X"},
        {"command": "undo_edit"},
    ],
)
def test_file_editor_restricted_blocks_outside(layout, kwargs):
    ex = FileEditorExecutor(
        workspace_root=str(layout["ws"]), restrict_to_workspace=True
    )
    text = _err_text(ex(FileEditorAction(path=str(layout["secret"]), **kwargs)))
    assert "outside the workspace" in text and CANARY not in text
    assert layout["secret"].read_text() == f"{CANARY}\nline2\n"


def test_file_editor_restricted_blocks_create_outside(layout):
    ex = FileEditorExecutor(
        workspace_root=str(layout["ws"]), restrict_to_workspace=True
    )
    target = layout["sibling"] / "planted.txt"
    _err_text(ex(FileEditorAction(command="create", path=str(target), file_text="x")))
    assert not target.exists()


def test_file_editor_restricted_allows_inside(layout):
    ex = FileEditorExecutor(
        workspace_root=str(layout["ws"]), restrict_to_workspace=True
    )
    new = layout["ws"] / "new.txt"  # nonexistent target
    obs = ex(FileEditorAction(command="create", path=str(new), file_text="hi"))
    assert not obs.is_error and new.read_text() == "hi"
    obs = ex(FileEditorAction(command="view", path=str(layout["ws"] / "inside.txt")))
    assert not obs.is_error and "inside" in obs.text


def test_file_editor_restricted_blocks_symlink(layout):
    (layout["ws"] / "link.txt").symlink_to(layout["secret"])
    ex = FileEditorExecutor(
        workspace_root=str(layout["ws"]), restrict_to_workspace=True
    )
    text = _err_text(
        ex(FileEditorAction(command="view", path=str(layout["ws"] / "link.txt")))
    )
    assert CANARY not in text


def test_file_editor_extra_allowed_root(layout):
    ex = FileEditorExecutor(
        workspace_root=str(layout["ws"]),
        restrict_to_workspace=True,
        allowed_roots=[str(layout["sibling"])],
    )
    obs = ex(FileEditorAction(command="view", path=str(layout["secret"])))
    assert not obs.is_error and CANARY in obs.text


def test_file_editor_restrict_requires_root():
    with pytest.raises(ValueError):
        FileEditorExecutor(restrict_to_workspace=True)


def test_planning_editor_restricted_keeps_plan_reachable(layout, tmp_path):
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    plan = plan_dir / "PLAN.md"
    plan.write_text("# Plan\n")
    ex = PlanningFileEditorExecutor(
        workspace_root=str(layout["ws"]),
        plan_path=str(plan),
        restrict_to_workspace=True,
    )
    assert not ex(PlanningFileEditorAction(command="view", path=str(plan))).is_error
    text = _err_text(
        ex(PlanningFileEditorAction(command="view", path=str(layout["secret"])))
    )
    assert CANARY not in text


def test_grep_restricted(layout):
    ex = GrepExecutor(working_dir=str(layout["ws"]), restrict_to_workspace=True)
    _err_text(ex(GrepAction(pattern="CANARY", path=str(layout["sibling"]))))
    (layout["ws"] / "linkdir").symlink_to(layout["sibling"], target_is_directory=True)
    obs = ex(GrepAction(pattern="CANARY|inside"))
    assert not obs.is_error
    assert all(
        Path(m).resolve().is_relative_to(layout["ws"].resolve()) for m in obs.matches
    )


def test_glob_restricted(layout):
    ex = GlobExecutor(working_dir=str(layout["ws"]), restrict_to_workspace=True)
    _err_text(ex(GlobAction(pattern="*.txt", path=str(layout["sibling"]))))
    _err_text(ex(GlobAction(pattern=f"{layout['sibling']}/*.txt")))
    (layout["ws"] / "linkdir").symlink_to(layout["sibling"], target_is_directory=True)
    obs = ex(GlobAction(pattern="**/*.txt"))
    assert all(
        Path(f).resolve().is_relative_to(layout["ws"].resolve()) for f in obs.files
    )


def test_gemini_tools_restricted(layout):
    root = str(layout["ws"])
    secret = str(layout["secret"])
    r = ReadFileExecutor(root, restrict_to_workspace=True)
    assert CANARY not in _err_text(r(ReadFileAction(file_path=secret)))
    assert CANARY not in _err_text(r(ReadFileAction(file_path="../ws2/secret.txt")))
    w = WriteFileExecutor(root, restrict_to_workspace=True)
    _err_text(
        w(WriteFileAction(file_path=str(layout["sibling"] / "w.txt"), content="x"))
    )
    assert not (layout["sibling"] / "w.txt").exists()
    e = EditExecutor(root, restrict_to_workspace=True)
    _err_text(e(EditAction(file_path=secret, old_string="line2", new_string="X")))
    ld = ListDirectoryExecutor(root, restrict_to_workspace=True)
    _err_text(ld(ListDirectoryAction(dir_path=str(layout["sibling"]))))
    assert not r(ReadFileAction(file_path="inside.txt")).is_error


# --- flag off: unchanged behavior (regression) --------------------------------


def test_default_off_behaves_as_before(layout):
    root = str(layout["ws"])
    secret = str(layout["secret"])
    fe = FileEditorExecutor(workspace_root=root)
    assert CANARY in fe(FileEditorAction(command="view", path=secret)).text
    assert (
        CANARY in ReadFileExecutor(root)(ReadFileAction(file_path=secret)).file_content
    )
    assert GrepExecutor(working_dir=root)(
        GrepAction(pattern="CANARY", path=str(layout["sibling"]))
    ).matches
    assert GlobExecutor(working_dir=root)(
        GlobAction(pattern="*.txt", path=str(layout["sibling"]))
    ).files


# --- apply_patch: unchanged ----------------------------------------------------


def test_apply_patch_still_rejects_escapes(layout):
    ex = ApplyPatchExecutor(workspace_root=str(layout["ws"]))
    for target in (f"{layout['sibling']}/x.txt", "../ws2/x.txt"):
        patch = f"*** Begin Patch\n*** Add File: {target}\n+x\n*** End Patch"
        obs = ex(ApplyPatchAction(patch=patch))
        assert obs.is_error and "escaping paths are not allowed" in obs.text
    assert not (layout["sibling"] / "x.txt").exists()


# --- wiring through Tool(params=...) -------------------------------------------


def test_tool_spec_params_reach_executor(layout):
    from uuid import uuid4

    from pydantic import SecretStr

    from openhands.sdk.agent import Agent
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.llm import LLM
    from openhands.sdk.tool import Tool, resolve_tool
    from openhands.sdk.workspace import LocalWorkspace
    from openhands.tools.file_editor import FileEditorTool

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="t")
    state = ConversationState.create(
        id=uuid4(),
        agent=Agent(llm=llm, tools=[]),
        workspace=LocalWorkspace(working_dir=str(layout["ws"])),
    )
    spec = Tool(name=FileEditorTool.name, params={"restrict_to_workspace": True})
    [tool] = resolve_tool(spec, state)
    assert tool.executor is not None
    obs = tool.executor(FileEditorAction(command="view", path=str(layout["secret"])))
    assert obs.is_error and CANARY not in obs.text
