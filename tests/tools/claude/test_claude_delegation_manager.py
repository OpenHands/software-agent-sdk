import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openhands.tools.claude.definition import (
    TaskAction,
    TaskOutputAction,
    TaskOutputObservation,
    TaskStopAction,
    TaskStopObservation,
)
from openhands.tools.claude.impl import (
    DelegationManager,
    TaskOutputExecutor,
    TaskState,
    TaskStatus,
    TaskStopExecutor,
)


class TestTaskStatusEnum:
    def test_all_values(self):
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.SUCCEEDED == "succeeded"
        assert TaskStatus.EMPTY_SUCCESS == "empty_success"
        assert TaskStatus.ERROR == "error"
        assert TaskStatus.STOPPED == "stopped"

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.RUNNING, str)
        assert f"status={TaskStatus.RUNNING}" == "status=running"


class TestTaskState:
    """Tests for TaskState dataclass."""

    def test_initial_state(self):
        """TaskState should start with 'running' status."""
        state = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        assert state.status == "running"
        assert state.result is None
        assert state.error is None
        assert state.thread is None

    def test_set_completed(self):
        """set_completed should update status and result."""
        state = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        state.set_completed("Done!")
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

    def test_stop_running_task(self):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.stop()
        assert task.status == TaskStatus.STOPPED
        assert task.result is None
        assert task.error is None

    def test_stop_completed_task_is_noop(self):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.set_completed("done")
        task.stop()
        # stop only affects RUNNING tasks
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result == "done"

    def test_stop_error_task_is_noop(self):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.set_error("boom")
        task.stop()
        assert task.status == TaskStatus.ERROR
        assert task.error == "boom"

    def test_stop_already_stopped_is_noop(self):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.stop()
        assert task.status == TaskStatus.STOPPED
        task.stop()
        assert task.status == TaskStatus.STOPPED

    def test_empty_string_gives_empty_success(self):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.set_completed("")
        assert task.status == TaskStatus.EMPTY_SUCCESS
        assert task.result == ""
        assert task.error is None

    def test_none_result_gives_empty_success(self):
        """None is falsy so should also yield EMPTY_SUCCESS."""
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        # Calling with explicit None via the internal path
        task.set_completed(None)  # type: ignore[arg-type]
        assert task.status == TaskStatus.EMPTY_SUCCESS

    def test_thread_safety(self):
        """set_completed and set_error should be thread-safe."""
        state = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        errors = []

        def set_completed():
            try:
                for _ in range(100):
                    state.set_completed("result")
            except Exception as e:
                errors.append(e)

        def set_error():
            try:
                for _ in range(100):
                    state.set_error("error")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=set_completed)
        t2 = threading.Thread(target=set_error)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # check that the final task is not corrupted
        assert len(errors) == 0
        assert state.status in ("completed", "error")

        if state.status == "error":
            assert state.error == "error"
            assert state.result is None
        else:
            assert state.error is None
            assert state.result == "result"


class TestTaskStatePersistence:
    """save_to_disk / load_from_disk round-trip."""

    def test_round_trip(self, tmp_path: Path):
        task = TaskState(
            id="t_persist",
            status=TaskStatus.RUNNING,
            conversation=None,
        )
        task.set_completed("hello world")

        task.save_to_disk(tmp_path / "task_state")
        loaded = TaskState.load_from_disk(tmp_path / "task_state")

        assert loaded is not None
        assert loaded.id == "t_persist"
        assert loaded.status == TaskStatus.SUCCEEDED
        assert loaded.result == "hello world"
        assert loaded.error is None
        # Non-serialized fields should be None after loading
        assert loaded.conversation is None
        assert loaded.thread is None

    def test_round_trip_error_state(self, tmp_path: Path):
        task = TaskState(id="t_err", status=TaskStatus.RUNNING, conversation=None)
        task.set_error("something failed")

        task.save_to_disk(tmp_path / "task_state")
        loaded = TaskState.load_from_disk(tmp_path / "task_state")

        assert loaded is not None
        assert loaded.status == TaskStatus.ERROR
        assert loaded.error == "something failed"
        assert loaded.result is None

    def test_round_trip_stopped_state(self, tmp_path: Path):
        task = TaskState(id="t_stop", status=TaskStatus.RUNNING, conversation=None)
        task.stop()

        task.save_to_disk(tmp_path / "task_state")
        loaded = TaskState.load_from_disk(tmp_path / "task_state")

        assert loaded is not None
        assert loaded.status == TaskStatus.STOPPED

    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        result = TaskState.load_from_disk(tmp_path / "nonexistent")
        assert result is None

    def test_load_adds_json_suffix(self, tmp_path: Path):
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.save_to_disk(tmp_path / "task_state")
        # load_from_disk should handle both with and without .json suffix
        loaded = TaskState.load_from_disk(tmp_path / "task_state")
        assert loaded is not None
        assert loaded.id == "t1"

    def test_save_is_atomic_via_tmp_file(self, tmp_path: Path):
        """save_to_disk should write via .tmp then rename."""
        task = TaskState(id="t1", status=TaskStatus.RUNNING, conversation=None)
        task.save_to_disk(tmp_path / "task_state")
        # .json file should exist, .tmp should not
        assert (tmp_path / "task_state.json").exists()
        assert not (tmp_path / "task_state.tmp").exists()


class TestClaudeDelegationManager:
    """Tests for DelegationManager."""

    def test_init_defaults(self):
        """Manager should initialize with correct defaults."""
        manager = DelegationManager()
        assert manager._max_tasks == 10
        assert len(manager._active_tasks) == 0
        assert manager._parent_conversation is None

    def test_init_custom_max_children(self):
        """Manager should accept custom max_tasks argument."""
        manager = DelegationManager(max_tasks=3)
        assert manager._max_tasks == 3

    @pytest.mark.parametrize("max_tasks", [0, -1])
    def test_invalid_max_tasks_zero(self, max_tasks: int):
        with pytest.raises(AssertionError):
            DelegationManager(max_tasks=max_tasks)

    def test_tmp_dir_created(self):
        manager = DelegationManager()
        assert manager._tmp_dir.exists()
        manager.close()
        assert not manager._tmp_dir.exists()

    def test_generate_task_id(self):
        """Generated task IDs should be unique and prefixed."""
        manager = DelegationManager()
        id1 = manager._generate_task_id()
        id2 = manager._generate_task_id()

        assert id1.startswith("task_")
        assert id2.startswith("task_")
        assert id1 != id2

    def test_parent_conversation_raises_before_set(self):
        """Accessing parent_conversation before first call should raise."""
        manager = DelegationManager()
        with pytest.raises(RuntimeError, match="Parent conversation not set"):
            _ = manager.parent_conversation

    def test_ensure_parent_sets_once(self):
        """_ensure_parent should only set the parent on the first call."""
        manager = DelegationManager()
        conv1 = MagicMock()
        conv2 = MagicMock()

        manager._ensure_parent(conv1)
        assert manager._parent_conversation is conv1

        manager._ensure_parent(conv2)
        assert manager._parent_conversation is conv1  # Still the first one

    def test_get_task_output_unknown_task_raises(self):
        """Getting output for unknown task should raise ValueError."""
        manager = DelegationManager()
        with pytest.raises(ValueError, match="not found"):
            _ = manager.get_task_output("nonexistent_task")

    def test_stop_task_unknown_returns_none(self):
        """Stopping unknown task should return None."""
        manager = DelegationManager()
        result = manager.stop_task("nonexistent_task")
        assert result is None

    def test_stop_task_running(self):
        """Stopping a running task should set status to 'stopped'."""
        manager = DelegationManager()
        task = TaskState(id="test_1", conversation=None, status=TaskStatus.RUNNING)
        manager._active_tasks["test_1"] = task
        manager._task_id_to_uuid["test_1"] = uuid.uuid4()

        result = manager.stop_task("test_1")
        assert result is not None
        assert result.status == "stopped"

    def test_get_task_output_completed(self):
        """Getting output for a completed task should return its result."""
        manager = DelegationManager()
        task = TaskState(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
        )
        task.set_completed("The result")
        assert task.status == "succeeded"
        assert task.result == "The result"

        # add task to manager and ask for result
        manager._active_tasks["test_1"] = task
        result = manager.get_task_output("test_1")
        assert result.status == "succeeded"
        assert result.result == "The result"
        assert result is task

    def test_get_task_output_nonblocking_running(self):
        """
        Non-blocking output check should
        return immediately for running task.
        """
        manager = DelegationManager()
        task = TaskState(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
        )
        # Leave it as running
        manager._active_tasks["test_1"] = task

        result = manager.get_task_output("test_1", block=False)
        assert result.status == "running"
        assert result.result is None


class TestTaskStopExecutor:
    """Tests for TaskStopExecutor."""

    def test_stop_unknown_task(self):
        """Stopping unknown task should return error observation."""
        manager = DelegationManager()
        executor = TaskStopExecutor(manager=manager)

        action = TaskStopAction(task_id="nonexistent")
        result = executor(action)

        assert isinstance(result, TaskStopObservation)
        assert result.is_error is True
        assert result.status == "not_found"

    def test_stop_existing_task(self):
        """Stopping an existing running task should succeed."""
        manager = DelegationManager()
        task = TaskState(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
        )
        manager._active_tasks["test_1"] = task
        manager._task_id_to_uuid["test_1"] = uuid.uuid4()

        executor = TaskStopExecutor(manager=manager)
        action = TaskStopAction(task_id="test_1")
        result = executor(action)

        assert isinstance(result, TaskStopObservation)
        assert result.is_error is False
        assert result.status == "stopped"
        assert result.task_id == "test_1"


class TestTaskOutputExecutor:
    """Tests for TaskOutputExecutor."""

    def test_output_unknown_task(self):
        """Getting output for unknown task should return error."""
        manager = DelegationManager()
        executor = TaskOutputExecutor(manager=manager)

        action = TaskOutputAction(task_id="nonexistent")
        result = executor(action)

        assert isinstance(result, TaskOutputObservation)
        assert result.is_error is True
        assert result.status == "error"

    def test_output_completed_task(self):
        """Getting output for completed task should return result."""
        manager = DelegationManager()
        task = TaskState(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
        )
        task.set_completed("The answer is 42")
        manager._active_tasks["test_1"] = task

        executor = TaskOutputExecutor(manager=manager)
        action = TaskOutputAction(task_id="test_1", block=False)
        result = executor(action)

        assert isinstance(result, TaskOutputObservation)
        assert result.is_error is False
        assert result.status == "completed"
        assert result.text == "The answer is 42"

    def test_output_running_task_nonblocking(self):
        """
        Non-blocking output check for running
        task should return immediately.
        """
        manager = DelegationManager()
        task = TaskState(
            id="test_1",
            conversation=None,
            status=TaskStatus.RUNNING,
        )
        manager._active_tasks["test_1"] = task

        executor = TaskOutputExecutor(manager=manager)
        action = TaskOutputAction(task_id="test_1", block=False)

        start = time.monotonic()
        result = executor(action)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0  # Should be near-instant
        assert result.status == "running"


class TestTaskAction:
    """Tests for TaskAction schema."""

    def test_required_prompt(self):
        """TaskAction should require a prompt."""
        action = TaskAction(prompt="Do something")
        assert action.prompt == "Do something"

    def test_defaults(self):
        """TaskAction should have sensible defaults."""
        action = TaskAction(prompt="test")
        assert action.subagent_type == "default"
        assert action.description is None
        assert action.run_in_background is False
        assert action.resume is None
        assert action.max_turns is None

    def test_all_params(self):
        """TaskAction should accept all parameters."""
        action = TaskAction(
            prompt="Run tests",
            subagent_type="researcher",
            description="Run unit tests",
            run_in_background=True,
            resume="task_abc123",
            max_turns=10,
        )
        assert action.prompt == "Run tests"
        assert action.subagent_type == "researcher"
        assert action.description == "Run unit tests"
        assert action.run_in_background is True
        assert action.resume == "task_abc123"
        assert action.max_turns == 10

    def test_max_turns_validation(self):
        """max_turns should be at least 1."""
        with pytest.raises(Exception):
            _ = TaskAction(prompt="test", max_turns=0)


class TestTaskOutputAction:
    """Tests for TaskOutputAction schema."""

    def test_required_task_id(self):
        """TaskOutputAction should require task_id."""
        action = TaskOutputAction(task_id="task_123")
        assert action.task_id == "task_123"

    def test_defaults(self):
        """TaskOutputAction should have sensible defaults."""
        action = TaskOutputAction(task_id="task_123")
        assert action.block is True
        assert action.timeout == 30000

    def test_timeout_validation(self):
        """timeout should be within bounds."""
        with pytest.raises(Exception):
            _ = TaskOutputAction(task_id="t", timeout=-1)
        with pytest.raises(Exception):
            _ = TaskOutputAction(task_id="t", timeout=700000)


class TestTaskStopAction:
    """Tests for TaskStopAction schema."""

    def test_required_task_id(self):
        """TaskStopAction should require task_id."""
        action = TaskStopAction(task_id="task_123")
        assert action.task_id == "task_123"
