"""Thread-safety tests for TaskManager.

These tests verify that TaskManager is safe under concurrent access.
Currently they FAIL because TaskManager has no locking — proving
that _generate_ids, _create_task, and _evict_task are not thread-safe.

Fix TaskManager with proper synchronization, then these tests will pass.
"""

import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
)
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task.manager import (
    Task,
    TaskManager,
    TaskStatus,
)


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


def _manager_with_parent(tmp_path: Path) -> tuple[TaskManager, LocalConversation]:
    manager = TaskManager()
    parent = _make_parent_conversation(tmp_path)
    manager._ensure_parent(parent)
    return manager, parent


def _make_task(task_id: str, **kwargs) -> Task:
    """Create a Task bypassing pydantic validation (allows mock conversation)."""
    defaults = {
        "id": task_id,
        "status": TaskStatus.RUNNING,
        "conversation_id": uuid.uuid4(),
        "result": None,
        "error": None,
        "conversation": None,
    }
    defaults.update(kwargs)
    return Task.model_construct(**defaults)


class TestGenerateIdsThreadSafety:
    def test_concurrent_generate_ids_are_unique(self, tmp_path: Path):
        """All concurrently generated task IDs must be unique."""
        manager, _ = _manager_with_parent(tmp_path)
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        results: list[str] = []
        lock = threading.Lock()

        def generate():
            barrier.wait(timeout=5)
            task_id, _ = manager._generate_ids()
            with lock:
                results.append(task_id)

        threads = [threading.Thread(target=generate) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == num_threads
        unique_ids = set(results)
        assert len(unique_ids) == num_threads, (
            f"Duplicate task IDs generated: got {len(unique_ids)} unique "
            f"out of {num_threads}. IDs: {results}"
        )


class TestCreateTaskThreadSafety:
    @pytest.fixture(autouse=True)
    def _register_agents(self):
        _reset_registry_for_tests()
        register_builtins_agents()
        yield
        _reset_registry_for_tests()

    def test_concurrent_create_tasks_all_preserved(self, tmp_path: Path):
        """All concurrently created tasks must be preserved in the dict
        with unique IDs — no lost updates, no overwrites."""
        manager, _ = _manager_with_parent(tmp_path)
        num_threads = 10
        barrier = threading.Barrier(num_threads)
        errors: list[Exception] = []
        created_tasks: list[Task] = []
        lock = threading.Lock()

        mock_conversation = MagicMock(spec=LocalConversation)
        mock_conversation.state.confirmation_policy = MagicMock()

        def create_task_thread():
            try:
                barrier.wait(timeout=5)
                with patch.object(
                    manager, "_get_conversation", return_value=mock_conversation
                ):
                    task = manager._create_task(
                        subagent_type="default",
                        description="test task",
                    )
                    with lock:
                        created_tasks.append(task)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=create_task_thread) for _ in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(created_tasks) == num_threads

        # Every task must have a unique ID
        task_ids = [t.id for t in created_tasks]
        unique_ids = set(task_ids)
        assert len(unique_ids) == num_threads, (
            f"Duplicate task IDs: got {len(unique_ids)} unique "
            f"out of {num_threads}. IDs: {task_ids}"
        )

        # Every task must be in the dict — no lost updates
        assert len(manager._tasks) == num_threads, (
            f"Lost updates: only {len(manager._tasks)} tasks in dict, "
            f"expected {num_threads}"
        )
