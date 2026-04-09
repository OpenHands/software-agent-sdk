"""Thread-safety tests for TaskManager under parallel tool execution.

These tests verify that guarantee by routing concurrent ``_create_task``
calls through the real ``ParallelToolExecutor`` and the real
``TaskTool.declared_resources()``.  A threading barrier inside
``_generate_ids`` forces all threads to read ``len(_tasks)`` at the same
instant, maximising the window for races.

If the internal locking in TaskManager is removed or broken, these tests
will fail with duplicate task IDs and lost dict updates.
"""

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.resource_lock_manager import ResourceLockManager
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.sdk.tool import ToolDefinition
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task.definition import TaskAction, TaskTool
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager


def _make_llm() -> LLM:
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )


def _make_parent_conversation(tmp_path: Path) -> LocalConversation:
    llm = _make_llm()
    agent = Agent(llm=llm, tools=[])
    return LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        delete_on_close=False,
    )


def _make_action_event(call_id: str) -> Any:
    """Create a mock ActionEvent carrying a real TaskAction."""
    ae = MagicMock()
    ae.tool_name = TaskTool.name
    ae.tool_call_id = call_id
    ae.action = TaskAction(prompt=f"do something ({call_id})")
    return ae


@pytest.fixture(autouse=True)
def _register_agents():
    _reset_registry_for_tests()
    register_builtins_agents()
    yield
    _reset_registry_for_tests()


NUM_CALLS = 10


def _run_concurrent_create_tasks(
    tmp_path: Path,
) -> tuple[TaskManager, list[str]]:
    """Run NUM_CALLS concurrent _create_task calls through
    ParallelToolExecutor using the real TaskTool.

    A barrier inside _generate_ids forces threads to hit
    len(_tasks) simultaneously, stressing the lock.
    """
    manager = TaskManager()
    parent = _make_parent_conversation(tmp_path)
    manager._ensure_parent(parent)

    mock_conversation = MagicMock(spec=LocalConversation)
    mock_conversation.state.confirmation_policy = MagicMock()

    created_ids: list[str] = []
    id_lock = threading.Lock()

    barrier = threading.Barrier(NUM_CALLS, timeout=10)
    original_generate_ids = manager._generate_ids

    def racy_generate_ids():
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return original_generate_ids()

    task_executor = TaskExecutor(manager=manager)
    task_tools = TaskTool.create(executor=task_executor, description="test")
    task_tool = task_tools[0]
    tools: dict[str, ToolDefinition] = {TaskTool.name: task_tool}

    action_events = [_make_action_event(f"call_{i}") for i in range(NUM_CALLS)]

    def tool_runner(ae: Any) -> list[Any]:
        with (
            patch.object(manager, "_get_conversation", return_value=mock_conversation),
            patch.object(manager, "_generate_ids", side_effect=racy_generate_ids),
        ):
            task = manager._create_task(
                subagent_type="default",
                description=f"task from {ae.tool_call_id}",
            )
            with id_lock:
                created_ids.append(task.id)
        return [MagicMock()]

    executor = ParallelToolExecutor(
        max_workers=NUM_CALLS,
        lock_manager=ResourceLockManager(),
    )
    executor.execute_batch(action_events, tool_runner, tools)

    return manager, created_ids


def test_concurrent_task_ids_are_unique(tmp_path: Path):
    """Concurrent _create_task calls must each produce a unique task ID.

    Without _tasks_lock, threads would read the same len(_tasks) and
    generate duplicate IDs like 'task_00000001' for every thread.
    """
    _, created_ids = _run_concurrent_create_tasks(tmp_path)

    unique_ids = set(created_ids)
    assert len(unique_ids) == NUM_CALLS, (
        f"Duplicate task IDs: got {len(unique_ids)} unique "
        f"out of {NUM_CALLS}. IDs: {created_ids}"
    )


def test_concurrent_tasks_all_preserved_in_dict(tmp_path: Path):
    """Concurrent _create_task calls must all survive in the _tasks dict.

    Without _tasks_lock, two threads generating the same ID would
    silently overwrite each other, losing tasks.
    """
    manager, _ = _run_concurrent_create_tasks(tmp_path)

    assert len(manager._tasks) == NUM_CALLS, (
        f"Lost updates: only {len(manager._tasks)} tasks in dict, "
        f"expected {NUM_CALLS}. "
        f"Keys: {list(manager._tasks.keys())}"
    )
