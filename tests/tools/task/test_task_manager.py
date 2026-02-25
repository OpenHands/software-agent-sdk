import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        assert manager._parent_conversation is None

    def test_tmp_dir_created(self):
        manager = TaskManager()
        assert manager._tmp_dir.exists()
        manager.close()
        assert not manager._tmp_dir.exists()

    def test_generate_task_id(self):
        """Generated task IDs should be unique and prefixed."""
        manager = TaskManager()

        tasks_ids: list[str] = []
        for j in range(10):
            id_, _ = manager._generate_ids()
            tasks_ids.append(id_)
            manager._tasks[id_] = Task(
                id=id_,
                conversation=None,
                status=TaskStatus.RUNNING,
                conversation_id=uuid.uuid4(),
            )
            assert id_.startswith("task_")

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


def _make_task_with_mock_conv(task_id: str, **conv_kwargs) -> Task:
    """Create a Task with a MagicMock conversation, bypassing Pydantic validation."""
    mock_conv = MagicMock(**conv_kwargs)
    return Task.model_construct(
        id=task_id,
        conversation_id=uuid.uuid4(),
        conversation=mock_conv,
        status=TaskStatus.RUNNING,
        result=None,
        error=None,
    )


class TestRunTask:
    """Tests for TaskManager._run_task."""

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def test_raises_when_conversation_is_none(self, tmp_path):
        """_run_task should raise RuntimeError if the task has no conversation."""
        manager, _ = _manager_with_parent(tmp_path)
        task = Task(
            id="task_00000001",
            conversation_id=uuid.uuid4(),
            conversation=None,
            status=TaskStatus.RUNNING,
        )
        with pytest.raises(RuntimeError, match="has no conversation"):
            manager._run_task(task=task, prompt="do something")

    @patch(
        "openhands.tools.task.manager.get_agent_final_response",
        return_value="task result",
    )
    def test_successful_run_sets_result(self, mock_get_response, tmp_path):
        """A successful run should set status to COMPLETED and populate result."""
        manager, _ = _manager_with_parent(tmp_path)

        task = _make_task_with_mock_conv("task_00000001")
        manager._tasks[task.id] = task

        result = manager._run_task(task=task, prompt="do something")

        assert result.status == TaskStatus.COMPLETED
        assert result.result == "task result"
        assert result.error is None
        conversation = task.conversation
        assert conversation is not None
        conversation.send_message.assert_called_once_with(  # type: ignore[attr-defined]
            "do something", sender=None
        )
        conversation.run.assert_called_once()  # type: ignore[attr-defined]

    @patch(
        "openhands.tools.task.manager.get_agent_final_response",
        return_value="task result",
    )
    def test_run_evicts_conversation_after_success(self, mock_get_response, tmp_path):
        """After a successful run, the task's conversation should be evicted."""
        manager, _ = _manager_with_parent(tmp_path)

        task = _make_task_with_mock_conv("task_00000001")
        mock_conv = task.conversation
        manager._tasks[task.id] = task

        manager._run_task(task=task, prompt="do something")

        # After eviction, the stored task should have no conversation
        assert manager._tasks[task.id].conversation is None
        assert mock_conv is not None
        mock_conv.pause.assert_called_once()  # type: ignore[attr-defined]
        mock_conv.close.assert_called_once()  # type: ignore[attr-defined]

    def test_run_sets_error_on_exception(self, tmp_path):
        """If the conversation raises, the task should be set to ERROR."""
        manager, _ = _manager_with_parent(tmp_path)

        task = _make_task_with_mock_conv(
            "task_00000001", **{"run.side_effect": RuntimeError("agent exploded")}
        )
        manager._tasks[task.id] = task

        result = manager._run_task(task=task, prompt="do something")

        assert result.status == TaskStatus.ERROR
        assert result.error is not None
        assert "agent exploded" in result.error
        assert result.result is None

    def test_run_evicts_conversation_after_error(self, tmp_path):
        """Even on error, the task's conversation should be evicted (finally block)."""
        manager, _ = _manager_with_parent(tmp_path)

        task = _make_task_with_mock_conv(
            "task_00000001", **{"run.side_effect": RuntimeError("boom")}
        )
        mock_conv = task.conversation
        manager._tasks[task.id] = task

        manager._run_task(task=task, prompt="do something")

        assert manager._tasks[task.id].conversation is None
        assert mock_conv is not None
        mock_conv.pause.assert_called_once()  # type: ignore[attr-defined]
        mock_conv.close.assert_called_once()  # type: ignore[attr-defined]

    @patch(
        "openhands.tools.task.manager.get_agent_final_response",
        return_value="done",
    )
    def test_run_passes_parent_visualizer_name_as_sender(
        self, mock_get_response, tmp_path
    ):
        """If parent has a visualizer with _name, it should be passed as sender."""
        manager, parent = _manager_with_parent(tmp_path)

        # Give the parent a visualizer with a _name
        mock_visualizer = MagicMock()
        mock_visualizer._name = "main-agent"
        parent._visualizer = mock_visualizer

        task = _make_task_with_mock_conv("task_00000001")
        manager._tasks[task.id] = task

        manager._run_task(task=task, prompt="hello")
        conversation = task.conversation
        assert conversation is not None
        task.conversation.send_message.assert_called_once_with(  # type: ignore[attr-defined]
            "hello", sender="main-agent"
        )


class TestStartTask:
    """Tests for TaskManager.start_task (create/resume dispatch + run)."""

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def _fake_run_task(self, task: Task, prompt: str) -> Task:
        """Simulate a successful _run_task without hitting the LLM."""
        task.set_result(f"result for: {prompt}")
        return task

    def test_start_new_task_creates_and_runs(self, tmp_path):
        """start_task without resume should create a new task and run it."""
        manager, parent = _manager_with_parent(tmp_path)

        with patch.object(manager, "_run_task", side_effect=self._fake_run_task):
            result = manager.start_task(
                prompt="do the thing",
                subagent_type="default",
                conversation=parent,
            )

        assert result.status == TaskStatus.COMPLETED
        assert result.result == "result for: do the thing"
        assert result.id.startswith("task_")
        assert result.id in manager._tasks

    def test_start_task_sets_parent_conversation(self, tmp_path):
        """start_task should set the parent conversation on first call."""
        manager = TaskManager()
        parent = _make_parent_conversation(tmp_path)

        assert manager._parent_conversation is None

        with patch.object(manager, "_run_task", side_effect=self._fake_run_task):
            manager.start_task(
                prompt="hello",
                subagent_type="default",
                conversation=parent,
            )

        assert manager._parent_conversation is parent

    def test_start_task_with_resume(self, tmp_path):
        """start_task with resume should resume an existing task."""
        manager, parent = _manager_with_parent(tmp_path)

        # Create and evict a task to simulate a prior completed run
        first = manager._create_task(
            subagent_type="default", description=None, max_turns=None
        )
        original_id = first.id
        manager._evict_task(first)

        with patch.object(manager, "_run_task", side_effect=self._fake_run_task):
            result = manager.start_task(
                prompt="continue",
                subagent_type="default",
                resume=original_id,
                conversation=parent,
            )

        assert result.status == TaskStatus.COMPLETED
        assert result.result == "result for: continue"
        assert result.id == original_id

    def test_start_task_resume_unknown_raises(self, tmp_path):
        """start_task with an unknown resume ID should raise ValueError."""
        manager, parent = _manager_with_parent(tmp_path)

        with pytest.raises(ValueError, match="not found"):
            manager.start_task(
                prompt="continue",
                subagent_type="default",
                resume="task_nonexistent",
                conversation=parent,
            )
