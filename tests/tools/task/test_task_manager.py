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
    TaskManager,
    TaskState,
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
        assert TaskStatus.SUCCEEDED == "succeeded"
        assert TaskStatus.EMPTY_SUCCESS == "empty_success"
        assert TaskStatus.ERROR == "error"

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.RUNNING, str)
        assert f"status={TaskStatus.RUNNING}" == "status=running"


class TestTaskState:
    """Tests for TaskState"""

    def test_initial_state(self):
        """TaskState should start with 'running' status."""
        state = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        assert state.status == "running"
        assert state.result is None
        assert state.error is None

    def test_set_completed(self):
        """set_completed should update status and result."""
        state = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        state.set_result("Done!")
        assert state.status == "succeeded"
        assert state.result == "Done!"
        assert state.error is None

    def test_set_error(self):
        """set_error should update status, error, and result."""
        state = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        state.set_error("Something went wrong")
        assert state.status == "error"
        assert state.error == "Something went wrong"
        assert state.result is None

    def test_empty_string_gives_empty_success(self):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.set_result("")
        assert task.status == TaskStatus.EMPTY_SUCCESS
        assert task.result == ""
        assert task.error is None


class TestTaskManager:
    """Tests for TaskManager."""

    def test_init_defaults(self):
        """Manager should initialize with correct defaults."""
        manager = TaskManager()
        assert len(manager._inactive_tasks) == 0
        assert len(manager._task_id_to_uuid) == 0
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
        id1 = manager._generate_task_id()
        id2 = manager._generate_task_id()

        assert id1.startswith("task_")
        assert id2.startswith("task_")
        assert id1 != id2

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
        assert manager._parent_conversation is conv1  # Still the first one

    def test_returns_running_task_state(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task = manager._create_task(
            subagent_type="default",
            description="test task",
            max_turns=3,
        )
        assert isinstance(task, TaskState)
        assert task.status == TaskStatus.RUNNING
        assert task.id.startswith("task_")
        assert task.conversation is not None
        assert task.result is None
        assert task.error is None

    def test_increments_counter(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        t1 = manager._create_task(
            subagent_type="default", description=None, max_turns=None
        )
        t2 = manager._create_task(
            subagent_type="default", description=None, max_turns=None
        )
        assert t1.id != t2.id
        assert manager._task_counter == 2

    def test_registers_uuid(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task = manager._create_task(
            subagent_type="default", description=None, max_turns=None
        )
        assert task.id in manager._task_id_to_uuid
        assert isinstance(manager._task_id_to_uuid[task.id], uuid.UUID)

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
        manager._evict_task(task)
        assert original_id in manager._inactive_tasks
        assert original_id in manager._task_id_to_uuid

        original_uuid = manager._task_id_to_uuid[task.id]

        # Resume it
        resumed = manager._resume_task(resume=original_id, subagent_type="default")
        assert resumed.id == original_id
        assert resumed.status == TaskStatus.RUNNING
        assert resumed.conversation is not None
        assert resumed.conversation.state.id == original_uuid

    def test_close(self):
        manager = TaskManager()
        assert manager._tmp_dir.exists()

        manager._inactive_tasks.add("task_abc123")
        manager._task_id_to_uuid["task_abc123"] = uuid.uuid4()

        manager.close()

        assert not manager._tmp_dir.exists()
        assert len(manager._inactive_tasks) == 0
        assert len(manager._task_id_to_uuid) == 0


# ---------------------------------------------------------------------------
# Tests for _get_sub_agent
# ---------------------------------------------------------------------------


class TestGetSubAgent:
    """Tests for TaskManager._get_sub_agent."""

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def test_default_agent_type(self, tmp_path):
        """'default' should return an agent without raising."""
        manager, _ = _manager_with_parent(tmp_path)
        agent = manager._get_sub_agent("default")
        assert isinstance(agent, Agent)

    def test_default_agent_disables_streaming(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        agent = manager._get_sub_agent("default")
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


# ---------------------------------------------------------------------------
# Tests for _get_conversation
# ---------------------------------------------------------------------------


class TestGetConversation:
    """Tests for TaskManager._get_conversation."""

    def test_returns_local_conversation(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id = manager._generate_task_id()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description="quiz",
            max_turns=5,
            task_id=task_id,
            worker_agent=agent,
        )
        assert isinstance(conv, LocalConversation)

    def test_max_turns_forwarded(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id = manager._generate_task_id()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description=None,
            max_turns=7,
            task_id=task_id,
            worker_agent=agent,
        )
        assert conv.max_iteration_per_run == 7

    def test_max_turns_defaults_to_500(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id = manager._generate_task_id()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description=None,
            max_turns=None,
            task_id=task_id,
            worker_agent=agent,
        )
        assert conv.max_iteration_per_run == 500

    def test_persistence_dir_is_tmp_dir(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id = manager._generate_task_id()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description=None,
            max_turns=None,
            task_id=task_id,
            worker_agent=agent,
        )
        # The conversation's persistence dir should be under the manager's tmp_dir
        persistence_dir = conv.state.persistence_dir
        assert persistence_dir is not None
        conv_persistence = Path(persistence_dir)
        assert str(conv_persistence).startswith(str(manager._tmp_dir))

    def test_delete_on_close_is_false(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id = manager._generate_task_id()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description=None,
            max_turns=None,
            task_id=task_id,
            worker_agent=agent,
        )
        assert conv.delete_on_close is False

    def test_no_visualizer_when_parent_has_none(self, tmp_path):
        manager, _ = _manager_with_parent(tmp_path)
        task_id = manager._generate_task_id()
        agent = manager._get_sub_agent("default")

        conv = manager._get_conversation(
            description="test",
            max_turns=None,
            task_id=task_id,
            worker_agent=agent,
        )
        assert conv._visualizer is None
