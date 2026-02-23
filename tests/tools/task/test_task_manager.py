import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.tools.delegate.registration import (
    _reset_registry_for_tests,
    register_agent,
)
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
    """Create a real (minimal) parent conversation for the manager."""
    llm = _make_llm()
    agent = Agent(llm=llm, tools=[])
    return LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        delete_on_close=False,
    )


def _manager_with_parent(tmp_path: Path) -> tuple[TaskManager, LocalConversation]:
    """Return a TaskManager whose parent conversation is already set."""
    manager = TaskManager()
    parent = _make_parent_conversation(tmp_path)
    manager._ensure_parent(parent)
    return manager, parent


class TestTaskStatusEnum:
    def test_all_values(self):
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.ERROR == "error"

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.RUNNING, str)
        assert f"status={TaskStatus.RUNNING}" == "status=running"


class TestTaskState:
    """Tests for TaskState"""

    def test_initial_state(self):
        """TaskState should start with 'running' status."""
        state = Task(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
            conversation_id=uuid.uuid4(),
        )
        assert state.status == "running"
        assert state.result is None
        assert state.error is None

    @pytest.mark.parametrize("result", ["Done!", ""])
    def test_set_completed(self, result):
        """set_completed should update status and result."""
        state = Task(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
            conversation_id=uuid.uuid4(),
        )
        state.set_result(result)
        assert state.status == "completed"
        assert state.result == result
        assert state.error is None

    def test_set_error(self):
        """set_error should update status, error, and result."""
        state = Task(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
            conversation_id=uuid.uuid4(),
        )
        state.set_error("Something went wrong")
        assert state.status == "error"
        assert state.error == "Something went wrong"
        assert state.result is None


class TestTaskManager:
    """Tests for TaskManager."""

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def test_init_defaults(self):
        """Manager should initialize with correct defaults."""
        manager = TaskManager()
        assert len(manager._tasks) == 0
        assert manager._task_counter == 0
        assert manager._parent_conversation is None

    def test_tmp_dir_created(self):
        manager = TaskManager()
        assert manager._tmp_dir.exists()
        manager.close()
        assert not manager._tmp_dir.exists()

    def test_generate_task_id(self):
        """Generated task IDs should be unique and prefixed."""
        manager = TaskManager()
        assert manager._task_counter == 0

        tasks_ids: list[str] = []
        for j in range(10):
            id_, _ = manager._generate_ids()
            tasks_ids.append(id_)
            assert id_.startswith("task_")
            assert manager._task_counter == j + 1

        assert len(tasks_ids) == len(set(tasks_ids))

    def test_parent_conversation_raises_before_set(self):
        """Accessing parent_conversation before first call should raise."""
        manager = TaskManager()
        with pytest.raises(RuntimeError, match="Parent conversation not set"):
            _ = manager.parent_conversation

    def test_ensure_parent_sets_once(self):
        """_ensure_parent should only set the parent on the first call."""
        manager = TaskManager()
        conv1 = MagicMock()
        conv2 = MagicMock()

        manager._ensure_parent(conv1)
        assert manager._parent_conversation is conv1

        manager._ensure_parent(conv2)
        # Still the first one
        assert manager._parent_conversation is conv1

    def test_returns_running_task_state(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task = manager._create_task(
            subagent_type="default",
            description="test task",
            max_turns=3,
        )
        assert isinstance(task, Task)
        assert task.status == TaskStatus.RUNNING
        assert task.id.startswith("task_")
        assert task.conversation is not None
        assert task.result is None
        assert task.error is None

    def test_registers_uuid(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task = manager._create_task(
            subagent_type="default", description=None, max_turns=None
        )
        assert task.id in manager._tasks
        assert isinstance(manager._tasks[task.id].conversation_id, uuid.UUID)

    def test_resume_unknown_task_raises(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            manager._resume_task(resume="task_nonexistent", subagent_type="default")

    def test_resume_after_evict(self, tmp_path):
        """A task that was created, evicted, and then resumed should work."""
        manager, _ = _manager_with_parent(tmp_path)

        # Create and evict a task (simulating a completed first run)
        task = manager._create_task(
            subagent_type="default", description=None, max_turns=None
        )
        original_id = task.id
        original_uuid = task.conversation_id
        manager._evict_task(task)
        assert original_id in manager._tasks

        # Resume it
        resumed = manager._resume_task(resume=original_id, subagent_type="default")
        assert resumed.id == original_id
        assert resumed.conversation_id == original_uuid
        assert resumed.status == TaskStatus.RUNNING
        assert resumed.conversation is not None
        assert resumed.conversation.state.id == original_uuid

    def test_default_agent_type(self, tmp_path):
        """'default' should return an agent without raising."""
        manager, _ = _manager_with_parent(tmp_path)
        agent = manager._get_sub_agent("default")
        assert isinstance(agent, Agent)
        assert agent.llm.stream is False

    def test_registered_agent_type(self, tmp_path):
        """A registered factory should produce the correct agent."""
        factory_called_with: list[LLM] = []

        def factory(llm: LLM) -> Agent:
            factory_called_with.append(llm)
            return Agent(llm=llm, tools=[])

        register_agent(
            name="test_expert",
            factory_func=factory,
            description="test",
        )

        manager, _ = _manager_with_parent(tmp_path)
        agent = manager._get_sub_agent("test_expert")
        assert isinstance(agent, Agent)
        assert len(factory_called_with) == 1
        assert factory_called_with[0].stream is False

    def test_unknown_agent_type_raises(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        with pytest.raises(ValueError, match="Unknown agent"):
            manager._get_sub_agent("nonexistent_agent")

    def test_close(self):
        manager = TaskManager()
        assert manager._tmp_dir.exists()

        manager._tasks["tasks_123"] = Task(
            id="tasks_123",
            conversation_id=uuid.uuid4(),
            status=TaskStatus.RUNNING,
        )

        manager.close()

        assert not manager._tmp_dir.exists()
        assert len(manager._tasks) == 0

    def test_returns_local_conversation(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id, conversation_id = manager._generate_ids()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description="quiz",
            task_id=task_id,
            worker_agent=agent,
            max_turns=None,
            conversation_id=conversation_id,
        )
        assert isinstance(conv, LocalConversation)
        assert conv.max_iteration_per_run == 500

    def test_persistence_dir_is_tmp_dir(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id, conversation_id = manager._generate_ids()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description=None,
            max_turns=None,
            task_id=task_id,
            worker_agent=agent,
            conversation_id=conversation_id,
        )
        # The conversation's persistence dir should be under the manager's tmp_dir
        persistence_dir = conv.state.persistence_dir
        assert persistence_dir is not None
        conv_persistence = Path(persistence_dir)
        assert str(conv_persistence).startswith(str(manager._tmp_dir))

    def test_no_visualizer_when_parent_has_none(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id, conversation_id = manager._generate_ids()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description="test",
            max_turns=None,
            task_id=task_id,
            conversation_id=conversation_id,
            worker_agent=agent,
        )
        assert conv._visualizer is None
