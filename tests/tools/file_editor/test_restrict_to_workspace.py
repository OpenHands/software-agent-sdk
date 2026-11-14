"""Opt-in confinement of file_editor paths to the conversation workspace."""

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool, resolve_tool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.file_editor import FileEditorAction, FileEditorTool
from openhands.tools.file_editor.impl import FileEditorExecutor


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "inside.txt").write_text("inside\n")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside.txt"
    path.write_text("outside\n")
    return path


def _file_editor(workspace: Path, **params: object) -> FileEditorTool:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    conv_state = ConversationState.create(
        id=uuid4(),
        agent=Agent(llm=llm, tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace)),
    )
    (tool,) = resolve_tool(Tool(name=FileEditorTool.name, params=params), conv_state)
    assert isinstance(tool, FileEditorTool)
    return tool


@pytest.mark.parametrize(
    "action",
    [
        {"command": "view"},
        {"command": "str_replace", "old_str": "outside", "new_str": "changed"},
        {"command": "insert", "insert_line": 0, "new_str": "changed\n"},
        {"command": "undo_edit"},
    ],
)
def test_existing_path_outside_workspace_is_refused(workspace, outside, action):
    tool = _file_editor(workspace, restrict_to_workspace=True)

    result = tool(FileEditorAction(path=str(outside), **action))

    assert result.is_error
    assert "outside the workspace" in result.text
    assert outside.read_text() == "outside\n"


def test_create_outside_workspace_is_refused(workspace, tmp_path):
    tool = _file_editor(workspace, restrict_to_workspace=True)
    target = tmp_path / "new.txt"

    result = tool(FileEditorAction(command="create", path=str(target), file_text="x"))

    assert result.is_error
    assert not target.exists()


def test_parent_traversal_out_of_workspace_is_refused(workspace, outside):
    tool = _file_editor(workspace, restrict_to_workspace=True)
    escaping = workspace / ".." / outside.name

    result = tool(FileEditorAction(command="view", path=str(escaping)))

    assert result.is_error
    assert "outside the workspace" in result.text


def test_symlink_inside_workspace_pointing_outside_is_refused(workspace, outside):
    tool = _file_editor(workspace, restrict_to_workspace=True)
    link = workspace / "link.txt"
    link.symlink_to(outside)

    result = tool(
        FileEditorAction(
            command="str_replace", path=str(link), old_str="outside", new_str="x"
        )
    )

    assert result.is_error
    assert outside.read_text() == "outside\n"


def test_relative_path_gets_the_usual_absolute_path_hint(workspace):
    tool = _file_editor(workspace, restrict_to_workspace=True)

    result = tool(FileEditorAction(command="view", path="inside.txt"))

    assert result.is_error
    assert "should be an absolute path" in result.text
    assert str(workspace / "inside.txt") in result.text


def test_directory_view_does_not_list_a_symlinked_directory_outside(
    workspace, tmp_path
):
    secret_dir = tmp_path / "elsewhere"
    secret_dir.mkdir()
    (secret_dir / "secret-name.txt").write_text("x")
    (workspace / "linked").symlink_to(secret_dir, target_is_directory=True)
    tool = _file_editor(workspace, restrict_to_workspace=True)

    result = tool(FileEditorAction(command="view", path=str(workspace)))

    assert not result.is_error
    assert "linked" in result.text
    assert "secret-name.txt" not in result.text


def test_symlink_loop_is_refused(workspace):
    (workspace / "loop-a").symlink_to(workspace / "loop-b")
    (workspace / "loop-b").symlink_to(workspace / "loop-a")
    tool = _file_editor(workspace, restrict_to_workspace=True)

    result = tool(FileEditorAction(command="view", path=str(workspace / "loop-a")))

    assert result.is_error


def test_paths_inside_workspace_still_work(workspace):
    tool = _file_editor(workspace, restrict_to_workspace=True)
    created = workspace / "sub" / "new.txt"
    created.parent.mkdir()

    assert not tool(FileEditorAction(command="view", path=str(workspace))).is_error
    assert not tool(
        FileEditorAction(command="create", path=str(created), file_text="hello\n")
    ).is_error
    replaced = tool(
        FileEditorAction(
            command="str_replace",
            path=str(workspace / "inside.txt"),
            old_str="inside",
            new_str="edited",
        )
    )

    assert not replaced.is_error
    assert created.read_text() == "hello\n"
    assert (workspace / "inside.txt").read_text() == "edited\n"


def test_default_keeps_paths_outside_workspace_reachable(workspace, outside):
    tool = _file_editor(workspace)

    result = tool(FileEditorAction(command="view", path=str(outside)))

    assert not result.is_error
    assert "outside" in result.text


def test_restriction_requires_a_workspace_root():
    with pytest.raises(ValueError, match="workspace_root"):
        FileEditorExecutor(restrict_to_workspace=True)
