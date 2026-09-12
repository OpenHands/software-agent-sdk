"""Task and Delegate behavior with independently owned remote workspaces."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

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
    barrier = Barrier(2, timeout=10)

    def factory(child_id, agent_type):
        barrier.wait()
        return env.factory(child_id, agent_type)

    manager = TaskManager(workspace_factory=factory)
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
        for task in tasks:
            workspace = task.remote_workspace
            assert workspace is not None
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
        assert resumed.conversation is original.conversation
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


@pytest.mark.parametrize("close_during_setup", [False, True])
def test_pending_workspace_ownership(remote_subagents, monkeypatch, close_during_setup):
    env = remote_subagents
    workspace = env.factory("worker", "remote-worker")
    entered, release = Event(), Event()
    original_enter = type(workspace).__enter__

    def enter(workspace):
        original_enter(workspace)
        entered.set()
        assert release.wait(10)
        return workspace

    monkeypatch.setattr(type(workspace), "__enter__", enter)
    manager = TaskManager(workspace_factory=lambda child, kind: workspace)
    manager.attach_parent(env.parent)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(manager.start_task, "Work", "remote-worker")
            try:
                assert entered.wait(10)
                if close_during_setup:
                    manager.close()
                else:
                    with pytest.raises(ValueError, match="distinct workspace"):
                        manager.start_task("Duplicate", "remote-worker")
                    assert env.lifecycle == [(workspace.host, "enter")]
            finally:
                release.set()
            if close_during_setup:
                with pytest.raises(RuntimeError, match="closed during task creation"):
                    pending.result()
            else:
                assert pending.result().status == TaskStatus.COMPLETED
    finally:
        manager.close()
    assert [step for _, step in env.lifecycle] == ["enter", "delete", "exit"]
    assert workspace._client is None


def test_missing_remote_task_never_creates_a_replacement(remote_subagents):
    env = remote_subagents
    manager = TaskManager(workspace_factory=env.factory)
    task = manager.start_task("Initial", "remote-worker", conversation=env.parent)
    workspace = env.workspaces[0]
    server = env.servers[workspace.host]
    server["conversations"].clear()
    try:
        result = TaskExecutor(manager)(TaskAction(prompt="Resume", resume=task.id))
        assert result.is_error
        assert "404" in result.text
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


def test_remote_budget_must_be_acknowledged_before_execution(remote_subagents):
    env = remote_subagents
    env.parent.max_budget_per_run = 1.0
    env.options.acknowledge_budget = False
    manager = TaskManager(workspace_factory=env.factory)
    try:
        with pytest.raises(ValueError, match="did not acknowledge max_budget_per_run"):
            manager.start_task("Work", "remote-worker", conversation=env.parent)
        server = env.servers[env.workspaces[0].host]
        assert not server["messages"]
        assert not server["conversations"]
        assert env.lifecycle[-1][1] == "exit"
    finally:
        manager.close()
