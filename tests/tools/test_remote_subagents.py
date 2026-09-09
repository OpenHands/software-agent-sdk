"""Task and Delegate behavior with independently owned remote workspaces."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation import LocalConversation, RemoteConversation
from openhands.sdk.security import AlwaysConfirm
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.task.definition import TaskAction, TaskToolSet
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager, TaskStatus


def test_remote_tasks_return_results_and_resume(remote_subagents):
    env = remote_subagents
    manager = TaskManager(workspace_factory=env.factory)
    manager.attach_parent(env.parent)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = list(
                pool.map(
                    lambda prompt: manager.start_task(prompt, "remote-worker"),
                    ["First task", "Second task"],
                )
            )
        assert isinstance(env.parent, LocalConversation)
        assert len(env.workspaces) == 2
        for task, workspace in zip(tasks, env.workspaces):
            assert task.status == TaskStatus.COMPLETED
            assert task.result == f"Result {task.id}"
            assert isinstance(task.conversation, RemoteConversation)
            assert task.conversation.workspace is workspace
            payload = env.servers[workspace.host]["creates"][0]
            assert payload["workspace"]["working_dir"] == workspace.working_dir
            assert payload["observability_metadata"]["parent_session_id"] == str(
                env.parent.id
            )
            assert payload["observability_metadata"]["task_id"] == task.id
        original = tasks[0]
        resumed = manager.start_task("Follow up", resume=original.id)
        assert resumed.status == TaskStatus.COMPLETED
        assert resumed.conversation_id == original.conversation_id
        assert resumed.conversation is not original.conversation
        assert len(env.workspaces) == 2
        assert len(env.servers[env.workspaces[0].host]["creates"]) == 1
    finally:
        manager.close()
    manager.close()
    for workspace in env.workspaces:
        assert [step for host, step in env.lifecycle if host == workspace.host] == [
            "enter",
            "delete",
            "exit",
        ]
        assert workspace._client is None


def test_remote_task_manifest_reconnects_after_restart(remote_subagents):
    env = remote_subagents
    first = TaskManager(workspace_factory=env.factory)
    task = first.start_task("Initial task", "remote-worker", conversation=env.parent)
    assert isinstance(task.conversation, RemoteConversation)
    workspace = env.workspaces[0]
    # Disconnect only the proxy, as happens when a client process goes away.
    task.conversation.delete_on_close = False
    task.conversation.close()
    restored = TaskManager(workspace_factory=lambda child, kind: workspace)
    restored.attach_parent(env.parent)
    try:
        resumed = restored.start_task("Continue", resume=task.id)
        assert resumed.status == TaskStatus.COMPLETED
        assert resumed.conversation_id == task.conversation_id
        assert len(env.servers[workspace.host]["creates"]) == 1
    finally:
        restored.close()


@pytest.mark.parametrize("restart", [False, True])
def test_missing_remote_task_never_creates_a_replacement(remote_subagents, restart):
    env = remote_subagents
    manager = TaskManager(workspace_factory=env.factory)
    task = manager.start_task("Initial", "remote-worker", conversation=env.parent)
    workspace = env.workspaces[0]
    server = env.servers[workspace.host]
    server["conversations"].clear()
    if restart:
        manager = TaskManager(workspace_factory=lambda child, kind: workspace)
        manager.attach_parent(env.parent)
    try:
        result = TaskExecutor(manager)(TaskAction(prompt="Resume", resume=task.id))
        assert result.is_error
        assert "no longer exists" in result.text
        assert len(server["creates"]) == 1
    finally:
        manager.close()


@pytest.mark.parametrize("tool", ["task", "delegate"])
@pytest.mark.parametrize("approve", [True, False])
def test_remote_confirmation_preserves_handler_behavior(
    remote_subagents, tool, approve
):
    env = remote_subagents
    env.options.confirm = True
    env.parent.set_confirmation_policy(AlwaysConfirm())
    confirmations = []

    def confirm(child_id, actions):
        confirmations.append((child_id, actions))
        return approve

    if tool == "task":
        executor = TaskManager(
            confirmation_handler=confirm, workspace_factory=env.factory
        )
        try:
            result = executor.start_task(
                "Work", "remote-worker", conversation=env.parent
            )
            assert result.status == TaskStatus.COMPLETED
        finally:
            executor.close()
    else:
        executor = DelegateExecutor(
            confirmation_handler=confirm, workspace_factory=env.factory
        )
        try:
            spawned = executor(
                DelegateAction(
                    command="spawn", ids=["worker"], agent_types=["remote-worker"]
                ),
                env.parent,
            )
            assert not spawned.is_error
            result = executor(
                DelegateAction(command="delegate", tasks={"worker": "Work"}), env.parent
            )
            assert "Agent worker: Result worker" in result.text
        finally:
            executor.close()
    assert len(confirmations) == 1
    assert len(confirmations[0][1]) == 1
    server = env.servers[env.workspaces[0].host]
    assert bool(server["rejections"]) is (not approve)


def test_delegate_runs_independent_remote_children_and_cleans_up(remote_subagents):
    env = remote_subagents
    executor = DelegateExecutor(workspace_factory=env.factory)
    try:
        spawned = executor(
            DelegateAction(
                command="spawn", ids=["a", "b"], agent_types=["remote-worker"] * 2
            ),
            env.parent,
        )
        assert not spawned.is_error
        result = executor(
            DelegateAction(command="delegate", tasks={"a": "One", "b": "Two"}),
            env.parent,
        )
        assert "1. Agent a: Result a\n2. Agent b: Result b" in result.text
        assert len(env.workspaces) == 2
        assert env.workspaces[0].host != env.workspaces[1].host
    finally:
        executor.close()
    for workspace in env.workspaces:
        assert [step for host, step in env.lifecycle if host == workspace.host] == [
            "enter",
            "delete",
            "exit",
        ]


@pytest.mark.parametrize("tool", ["task", "delegate"])
@pytest.mark.parametrize("failure", ["fail_create", "fail_policy", "fail_events"])
def test_failed_remote_setup_releases_workspace(remote_subagents, tool, failure):
    env = remote_subagents
    setattr(env.options, failure, True)
    if tool == "task":
        executor = TaskManager(workspace_factory=env.factory)
        with pytest.raises(httpx.HTTPStatusError, match="400 Bad Request"):
            executor.start_task("Work", "remote-worker", conversation=env.parent)
    else:
        executor = DelegateExecutor(workspace_factory=env.factory)
        result = executor(
            DelegateAction(
                command="spawn", ids=["worker"], agent_types=["remote-worker"]
            ),
            env.parent,
        )
        assert result.is_error
    executor.close()
    assert env.lifecycle[-1][1] == "exit"
    assert env.workspaces[0]._client is None
    assert not env.servers[env.workspaces[0].host]["conversations"]


def test_parent_tool_cleanup_releases_remote_children(remote_subagents):
    env = remote_subagents
    tool = TaskToolSet.create(env.parent.state, workspace_factory=env.factory)[0]
    register_tool("remote_tasks_test", tool)
    parent = LocalConversation(
        agent=Agent(llm=env.parent.agent.llm, tools=[Tool(name="remote_tasks_test")]),
        workspace=env.parent.state.workspace,
        visualizer=None,
    )
    try:
        result = parent.execute_tool(
            tool.name, TaskAction(prompt="Work", subagent_type="remote-worker")
        )
        assert not result.is_error
        assert "Result task_00000001" in result.text
    finally:
        parent.close()
    assert env.lifecycle[-1][1] == "exit"
    assert not env.servers[env.workspaces[0].host]["conversations"]


def test_reopened_parent_fails_cleanly_without_workspace_resolver(remote_subagents):
    env = remote_subagents
    manager = TaskManager(workspace_factory=env.factory)
    task = manager.start_task("Work", "remote-worker", conversation=env.parent)
    manager.close()
    parent_id = env.parent.id
    persistence_dir = Path(env.parent.state.persistence_dir).parent
    env.parent.close()
    with closing(
        LocalConversation(
            agent=env.parent.agent,
            workspace=env.parent.state.workspace,
            conversation_id=parent_id,
            persistence_dir=persistence_dir,
            visualizer=None,
        )
    ) as parent:
        restored = TaskManager()
        try:
            result = TaskExecutor(restored)(
                TaskAction(prompt="Resume", resume=task.id), parent
            )
            assert result.is_error
            assert "requires its workspace_factory" in result.text
        finally:
            restored.close()


def test_reusing_a_workspace_is_rejected_without_closing_its_owner(remote_subagents):
    env = remote_subagents
    workspace = env.factory("first", "remote-worker")
    manager = TaskManager(workspace_factory=lambda child, kind: workspace)
    try:
        first = manager.start_task("Work", "remote-worker", conversation=env.parent)
        with pytest.raises(ValueError, match="distinct workspace"):
            manager.start_task("More work", "remote-worker")
        assert "exit" not in [step for _, step in env.lifecycle]
        resumed = manager.start_task("Continue", resume=first.id)
        assert resumed.status == TaskStatus.COMPLETED
    finally:
        manager.close()
