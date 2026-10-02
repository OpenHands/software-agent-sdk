"""Exercise both delegation entrypoints with real conversations and TestLLM.

Run the credential-free demo with:
    uv run python tests/tools/task/test_delegation_capabilities.py
"""

import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.subagent.capabilities import SubagentCapabilityLimits
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.delegate.impl import DelegateExecutor
from openhands.tools.task.definition import TaskAction
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager


def _worker(llm: LLM) -> Agent:
    return Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="Allowed task done")])]
        ),
        tools=[],
    )


def _blocked_worker(llm: LLM) -> Agent:
    # An unregistered tool makes it especially clear that validation runs
    # before the SDK attempts to resolve or construct the requested tool.
    return Agent(llm=llm, tools=[Tool(name="forbidden_write")])


def _parent(workspace: Path) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=TestLLM(model="test-model"),
            subagent_capability_limits=SubagentCapabilityLimits(
                tool_names=(), mcp_server_names=()
            ),
        ),
        workspace=workspace,
        persistence_dir=workspace / "state",
        visualizer=None,
    )


@pytest.fixture(autouse=True)
def registered_workers():
    _reset_registry_for_tests()
    register_agent("allowed", _worker, "Allowed worker")
    register_agent("blocked", _blocked_worker, "Worker outside the parent ceiling")
    yield
    _reset_registry_for_tests()


def test_task_reports_denial_and_still_runs_allowed_worker(tmp_path: Path) -> None:
    with closing(_parent(tmp_path)) as parent, closing(TaskManager()) as manager:
        execute = TaskExecutor(manager)
        denied = execute(TaskAction(prompt="write", subagent_type="blocked"), parent)
        assert denied.is_error and "forbidden_write" in denied.text
        assert manager._tasks == {}
        result = execute(TaskAction(prompt="read", subagent_type="allowed"), parent)
        assert not result.is_error and "Allowed task done" in result.text
        resumed = execute(
            TaskAction(
                prompt="read again", subagent_type="allowed", resume=result.task_id
            ),
            parent,
        )
        assert not resumed.is_error
        task = manager._tasks[result.task_id]
        assert manager._persistence_dir is not None
        with closing(
            LocalConversation(
                agent=None,
                workspace=tmp_path,
                persistence_dir=manager._persistence_dir,
                conversation_id=task.conversation_id,
                visualizer=None,
            )
        ) as child:
            nested = TaskExecutor(TaskManager())
            try:
                denied_nested = nested(
                    TaskAction(prompt="write", subagent_type="blocked"), child
                )
                assert (
                    denied_nested.is_error and "forbidden_write" in denied_nested.text
                )
            finally:
                nested.close()


def test_delegate_reports_denial_and_persists_inherited_limits(tmp_path: Path) -> None:
    with closing(_parent(tmp_path)) as parent, closing(DelegateExecutor()) as execute:
        denied = execute(
            DelegateAction(command="spawn", ids=["bad"], agent_types=["blocked"]),
            parent,
        )
        assert denied.is_error and "forbidden_write" in denied.text
        assert execute._sub_agents == {}
        spawned = execute(
            DelegateAction(command="spawn", ids=["ok"], agent_types=["allowed"]), parent
        )
        assert not spawned.is_error
        child = execute._sub_agents["ok"]
        assert child.agent.subagent_capability_limits is not None
        result = execute(
            DelegateAction(command="delegate", tasks={"ok": "read"}), parent
        )
        assert not result.is_error and "Allowed task done" in result.text
        with closing(DelegateExecutor()) as nested:
            denied_nested = nested(
                DelegateAction(command="spawn", ids=["bad"], agent_types=["blocked"]),
                child,
            )
            assert denied_nested.is_error


def demo() -> None:
    """Demonstrate allowed and denied delegation without credentials or MCP."""
    _reset_registry_for_tests()
    register_agent("allowed", _worker, "Allowed worker")
    register_agent("blocked", _blocked_worker, "Disallowed worker")
    try:
        with TemporaryDirectory(prefix="capability-demo-") as directory:
            with (
                closing(_parent(Path(directory))) as parent,
                closing(TaskManager()) as manager,
            ):
                execute = TaskExecutor(manager)
                allowed = execute(
                    TaskAction(prompt="read", subagent_type="allowed"), parent
                )
                denied = execute(
                    TaskAction(prompt="write", subagent_type="blocked"), parent
                )
                assert not allowed.is_error and denied.is_error
                print(
                    json.dumps(
                        {
                            "allowed_status": allowed.status,
                            "blocked_status": denied.status,
                            "blocked_reason": denied.text,
                            "created_tasks": len(manager._tasks),
                        },
                        indent=2,
                    )
                )
    finally:
        _reset_registry_for_tests()


if __name__ == "__main__":
    demo()
