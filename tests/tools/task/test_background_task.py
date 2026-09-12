"""Behavior tests for background tasks exposed by TaskToolSet."""

import asyncio
import shutil
import threading
import uuid
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr, SecretStr, ValidationError

from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import InterruptEvent, MessageEvent
from openhands.sdk.llm import LLMResponse, Message, TextContent
from openhands.sdk.llm.utils.metrics import Metrics, MetricsSnapshot, TokenUsage
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    register_agent,
)
from openhands.tools.task.definition import (
    TaskAction,
    TaskOutputAction,
    TaskOutputObservation,
    TaskStopAction,
    TaskStopObservation,
    TaskToolSet,
)
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import Task, TaskManager, TaskStatus


def _make_parent(
    tmp_path: Path,
    *,
    persistence_dir: Path | None = None,
    tools: list[Any] | None = None,
) -> LocalConversation:
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id=f"parent-{uuid.uuid4().hex}",
    )
    return LocalConversation(
        agent=Agent(llm=llm, tools=tools or []),
        workspace=str(tmp_path),
        visualizer=None,
        persistence_dir=persistence_dir,
        delete_on_close=False,
    )


@pytest.fixture
def task_runtime(tmp_path: Path):
    parent = _make_parent(tmp_path)
    manager = TaskManager()
    manager.attach_parent(parent)
    yield manager, parent
    manager.close()
    parent.close()


@pytest.fixture(autouse=True)
def no_background_worker_leaks():
    existing = {id(thread) for thread in threading.enumerate()}
    yield
    leaked = [
        thread
        for thread in threading.enumerate()
        if id(thread) not in existing
        and thread.name.startswith("Task-task_")
        and thread.is_alive()
    ]
    assert leaked == []


def _agent_message(text: str) -> MessageEvent:
    return MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[TextContent(text=text)],
        ),
    )


class _ControlledConversation:
    """Small async conversation double with event-driven completion."""

    def __init__(
        self,
        result: str = "done",
        *,
        error: BaseException | None = None,
        interrupt_releases: bool = True,
        metrics: Metrics | None = None,
    ) -> None:
        self.state = SimpleNamespace(
            execution_status=ConversationExecutionStatus.IDLE,
            events=[_agent_message(result)] if result else [],
        )
        self.result = result
        self.error = error
        self.interrupt_releases = interrupt_releases
        self.started = threading.Event()
        self.closed = threading.Event()
        self.interrupted = threading.Event()
        self.sent_messages: list[tuple[str, str | None]] = []
        self.close_count = 0
        self.pause_count = 0
        self.interrupt_count = 0
        self.conversation_stats = MagicMock()
        self.conversation_stats.get_combined_metrics.return_value = metrics or Metrics()
        self._signal_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._release_event: asyncio.Event | None = None
        self._released = False

    def send_message(self, prompt: str, sender: str | None = None) -> None:
        self.sent_messages.append((prompt, sender))

    def run(self) -> None:
        self.started.set()
        if self.error is not None:
            raise self.error
        self.state.execution_status = ConversationExecutionStatus.FINISHED

    async def arun(self) -> None:
        loop = asyncio.get_running_loop()
        release_event = asyncio.Event()
        with self._signal_lock:
            self._loop = loop
            self._release_event = release_event
            if self._released:
                release_event.set()
        self.state.execution_status = ConversationExecutionStatus.RUNNING
        self.started.set()
        await release_event.wait()
        if self.error is not None:
            raise self.error
        if self.state.execution_status != ConversationExecutionStatus.PAUSED:
            self.state.execution_status = ConversationExecutionStatus.FINISHED

    def finish(self) -> None:
        with self._signal_lock:
            self._released = True
            loop = self._loop
            release_event = self._release_event
        if loop is not None and release_event is not None:
            loop.call_soon_threadsafe(release_event.set)

    def interrupt(self) -> None:
        self.interrupt_count += 1
        self.interrupted.set()
        self.state.execution_status = ConversationExecutionStatus.PAUSED
        if self.interrupt_releases:
            self.finish()

    def pause(self) -> None:
        self.pause_count += 1

    def close(self) -> None:
        self.close_count += 1
        self.closed.set()


class _ProbeBaseException(BaseException):
    """BaseException used to verify worker/cleanup containment."""


class _ConfirmationConversation(_ControlledConversation):
    def __init__(self) -> None:
        super().__init__("approved result")
        self.run_count = 0

    async def arun(self) -> None:
        self.run_count += 1
        self.started.set()
        if self.run_count == 1:
            self.state.execution_status = (
                ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            )
        else:
            self.state.execution_status = ConversationExecutionStatus.FINISHED


def _install_conversations(
    manager: TaskManager,
    conversations: list[_ControlledConversation],
) -> None:
    remaining = deque(conversations)

    def create_task(
        subagent_type: str,
        description: str | None,  # noqa: ARG001
        status: TaskStatus = TaskStatus.RUNNING,
    ) -> Task:
        task_id, conversation_id = manager._generate_ids()
        conversation = remaining.popleft()
        task = Task.model_construct(
            id=task_id,
            status=status,
            subagent=subagent_type,
            conversation_id=conversation_id,
            result=None,
            error=None,
            conversation=cast(LocalConversation, conversation),
            thread=None,
            completion_event=threading.Event(),
            wait_event=threading.Event(),
            stop_requested=False,
            metrics_settled=False,
            settlement_started=False,
            settled=False,
        )
        manager._tasks[task_id] = task
        manager._reserve_parent_metrics(task_id)
        return task

    manager._create_task = create_task  # type: ignore[method-assign]


def _start_background(
    manager: TaskManager,
    parent: LocalConversation,
    *,
    prompt: str = "work",
    subagent_type: str = "test-agent",
) -> Task:
    return manager.start_task(
        prompt=prompt,
        subagent_type=subagent_type,
        conversation=parent,
        run_in_background=True,
    )


def _worker_for(manager: TaskManager, task_id: str) -> threading.Thread:
    worker = manager._tasks[task_id].thread
    assert worker is not None
    return worker


class _TrackingEvent(threading.Event):
    def __init__(self, entered: threading.Event) -> None:
        super().__init__()
        self._entered = entered

    def wait(self, timeout: float | None = None) -> bool:
        self._entered.set()
        return super().wait(timeout)


def test_task_tool_set_exposes_one_shared_background_lifecycle() -> None:
    tools = TaskToolSet.create(conv_state=None)  # type: ignore[arg-type]

    assert [tool.name for tool in tools] == ["task", "task_output", "task_stop"]
    assert "run_in_background" in TaskAction.model_fields
    assert TaskAction(prompt="work").run_in_background is False
    assert TaskAction(prompt="work").subagent_type == "default"
    assert len({id(tool.executor) for tool in tools}) == 1


def test_background_start_returns_before_controlled_run_finishes(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("background result")
    _install_conversations(manager, [conversation])

    task = _start_background(manager, parent)

    assert task.status == TaskStatus.QUEUED
    assert not conversation.closed.is_set()
    assert conversation.started.wait(timeout=2)
    assert manager.get_task(task.id).status == TaskStatus.RUNNING

    conversation.finish()
    completed = manager.get_task(task.id, block=True, timeout=2)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.result == "background result"


def test_native_output_tool_polls_waits_and_repeats_without_side_effects(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("tool result")
    _install_conversations(manager, [conversation])
    executor = TaskExecutor(manager)

    with patch(
        "openhands.tools.task.manager.get_agent_final_response",
        wraps=get_agent_final_response,
    ) as extract_response:
        started = executor(
            TaskAction(prompt="work", run_in_background=True),
            conversation=parent,
        )
        assert conversation.started.wait(timeout=2)

        running = executor(TaskOutputAction(task_id=started.task_id))
        assert isinstance(running, TaskOutputObservation)
        assert running.status == TaskStatus.RUNNING
        assert running.is_error is False

        conversation.finish()
        first = executor(
            TaskOutputAction(task_id=started.task_id, block=True, timeout=2)
        )
        second = executor(TaskOutputAction(task_id=started.task_id))
    assert first.status == second.status == TaskStatus.COMPLETED
    assert first.text == second.text == "tool result"
    extract_response.assert_called_once()
    conversation.conversation_stats.get_combined_metrics.assert_called_once()


def test_background_failure_is_terminal_and_readable(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(error=RuntimeError("agent exploded"))
    _install_conversations(manager, [conversation])

    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()

    failed = manager.get_task(task.id, block=True, timeout=2)
    assert failed.status == TaskStatus.ERROR
    assert failed.error is not None
    assert "agent exploded" in failed.error
    assert conversation.close_count == 1


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(2)])
def test_background_base_exception_is_terminal_and_readable(
    task_runtime, failure: BaseException
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(error=failure)
    _install_conversations(manager, [conversation])

    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()

    failed = manager.get_task(task.id, block=True, timeout=2)
    assert failed.status == TaskStatus.ERROR
    assert failed.error is not None
    assert failed.error == (str(failure) or type(failure).__name__)
    assert conversation.close_count == 1


def test_stop_is_cooperative_and_repeated_stop_is_idempotent(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("partial result")
    _install_conversations(manager, [conversation])
    executor = TaskExecutor(manager)

    started = executor(
        TaskAction(prompt="work", run_in_background=True),
        conversation=parent,
    )
    assert conversation.started.wait(timeout=2)

    first = executor(TaskStopAction(task_id=started.task_id))
    second = executor(TaskStopAction(task_id=started.task_id))
    assert isinstance(first, TaskStopObservation)
    assert first.status == second.status == TaskStatus.CANCELLED
    assert first.is_error is second.is_error is False
    assert conversation.interrupt_count == 1
    assert conversation.close_count == 1
    assert not _worker_for(manager, started.task_id).is_alive()


def test_close_wakes_blocking_output_reader(task_runtime, monkeypatch) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(interrupt_releases=False)
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)

    wait_entered = threading.Event()
    tracked_wait = _TrackingEvent(wait_entered)
    manager._tasks[task.id].wait_event = tracked_wait
    reader = threading.Thread(
        target=lambda: manager.get_task(task.id, block=True, timeout=60),
        name="blocking-task-output-reader",
    )
    reader.start()
    assert wait_entered.wait(timeout=2)

    monkeypatch.setattr(
        "openhands.tools.task.manager._TASK_STOP_TIMEOUT_SECONDS",
        0.0,
    )
    manager.close()
    reader.join(timeout=0.5)
    assert not reader.is_alive()

    # Let the cooperative worker finish so the fixture can complete cleanup.
    conversation.finish()
    worker = _worker_for(manager, task.id)
    worker.join(timeout=2)
    assert not worker.is_alive()
    reader.join(timeout=2)
    manager.close()


def test_queued_task_can_be_stopped_before_conversation_run(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])
    worker_entered = threading.Event()
    allow_worker = threading.Event()
    stop_requested = threading.Event()
    original_worker = manager._run_background_task
    original_interrupt = manager._interrupt_conversation

    def gated_worker(task_id: str, prompt: str, start_gate: threading.Event) -> None:
        worker_entered.set()
        assert allow_worker.wait(timeout=2)
        original_worker(task_id, prompt, start_gate)

    def track_interrupt(task_id: str, sub_conversation: LocalConversation) -> None:
        stop_requested.set()
        original_interrupt(task_id, sub_conversation)

    manager._run_background_task = gated_worker  # type: ignore[method-assign]
    manager._interrupt_conversation = track_interrupt  # type: ignore[method-assign]
    task = _start_background(manager, parent)
    assert task.status == TaskStatus.QUEUED
    assert worker_entered.wait(timeout=2)
    stopped: list[Task] = []
    stopper = threading.Thread(
        target=lambda: stopped.append(manager.stop_task(task.id)),
        name="queued-task-stopper",
    )
    stopper.start()
    assert stop_requested.wait(timeout=2)
    assert manager.get_task(task.id).status == TaskStatus.QUEUED

    allow_worker.set()
    stopper.join(timeout=2)
    assert not stopper.is_alive()
    assert stopped[0].status == TaskStatus.CANCELLED
    assert not conversation.started.is_set()


def test_stop_accepted_before_close_returns_cancelled_without_registry_race(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(interrupt_releases=False)
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    interrupt_seen = threading.Event()
    release_stop = threading.Event()
    original_interrupt = manager._interrupt_conversation

    def gated_interrupt(task_id: str, sub_conversation: LocalConversation) -> None:
        original_interrupt(task_id, sub_conversation)
        interrupt_seen.set()
        assert release_stop.wait(timeout=2)

    manager._interrupt_conversation = gated_interrupt  # type: ignore[method-assign]
    stopped: list[Task] = []
    stopper = threading.Thread(
        target=lambda: stopped.append(manager.stop_task(task.id)),
        name="closing-task-stopper",
    )
    stopper.start()
    assert interrupt_seen.wait(timeout=2)

    closer = threading.Thread(target=manager.close, name="task-manager-closer")
    closer.start()
    conversation.finish()
    release_stop.set()
    stopper.join(timeout=2)
    closer.join(timeout=2)

    assert not stopper.is_alive()
    assert not closer.is_alive()
    assert stopped[0].status == TaskStatus.CANCELLED
    assert manager._cleanup_complete is True


def test_unknown_and_terminal_operations_have_deterministic_errors(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    executor = TaskExecutor(manager)

    missing_output = executor(TaskOutputAction(task_id="task_missing"))
    missing_stop = executor(TaskStopAction(task_id="task_missing"))
    assert missing_output.is_error is True
    assert "not found" in missing_output.text
    assert missing_stop.is_error is True
    assert "not found" in missing_stop.text

    conversation = _ControlledConversation("done")
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()
    assert manager.get_task(task.id, block=True, timeout=2).status == (
        TaskStatus.COMPLETED
    )

    terminal_stop = executor(TaskStopAction(task_id=task.id))
    assert terminal_stop.is_error is True
    assert "cannot be stopped" in terminal_stop.text


def test_lifecycle_operations_are_scoped_to_the_bound_parent(
    task_runtime,
    tmp_path: Path,
) -> None:
    manager, parent = task_runtime
    other_parent = _make_parent(tmp_path / "other-parent")
    conversation = _ControlledConversation("isolated")
    _install_conversations(manager, [conversation])
    executor = TaskExecutor(manager)

    try:
        started = executor(
            TaskAction(prompt="work", run_in_background=True),
            conversation=parent,
        )
        assert conversation.started.wait(timeout=2)

        output = executor(
            TaskOutputAction(task_id=started.task_id),
            conversation=other_parent,
        )
        stop = executor(
            TaskStopAction(task_id=started.task_id),
            conversation=other_parent,
        )

        assert output.is_error is True
        assert "different parent" in output.text
        assert stop.is_error is True
        assert "different parent" in stop.text
        assert manager.get_task(started.task_id).status == TaskStatus.RUNNING
        conversation.finish()
        assert manager.get_task(started.task_id, block=True, timeout=2).status == (
            TaskStatus.COMPLETED
        )
    finally:
        other_parent.close()


@pytest.mark.parametrize("timeout", [-1.0, 3600.1, float("inf"), float("nan")])
def test_output_timeout_rejects_out_of_range_values(timeout: float) -> None:
    with pytest.raises(ValidationError):
        TaskOutputAction(task_id="task_1", block=True, timeout=timeout)

    with pytest.raises(ValueError, match="between 0 and 3600"):
        TaskManager().get_task("task_1", block=True, timeout=timeout)


@pytest.mark.parametrize("action_type", [TaskOutputAction, TaskStopAction])
def test_lifecycle_actions_reject_empty_task_ids(action_type: type[Any]) -> None:
    with pytest.raises(ValidationError):
        action_type(task_id="")


def test_parent_task_numbers_continue_after_restored_metric_keys(
    tmp_path: Path,
) -> None:
    parent = _make_parent(tmp_path)
    parent.conversation_stats.usage_to_metrics["task:task_0000000f"] = Metrics()
    manager = TaskManager()
    manager.attach_parent(parent)
    try:
        task_id, _ = manager._generate_ids()
        assert task_id == "task_00000010"
    finally:
        manager.close()
        parent.close()


def test_parent_task_id_scan_snapshots_concurrent_metrics_updates(
    tmp_path: Path,
) -> None:
    parent = _make_parent(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class ConcurrentMetrics(dict[str, Metrics]):
        def __iter__(self):  # type: ignore[override]
            entered.set()
            assert release.wait(timeout=2)
            return super().__iter__()

        def copy(self):  # type: ignore[override]
            entered.set()
            assert release.wait(timeout=2)
            return dict.copy(self)

    parent.conversation_stats.usage_to_metrics = ConcurrentMetrics(
        {"task:task_00000001": Metrics()}
    )
    manager = TaskManager()
    errors: list[BaseException] = []

    def attach() -> None:
        try:
            manager.attach_parent(parent)
        except BaseException as error:  # pragma: no cover - assertion below
            errors.append(error)

    thread = threading.Thread(target=attach)
    thread.start()
    assert entered.wait(timeout=2)
    parent.conversation_stats.usage_to_metrics["task:task_00000002"] = Metrics()
    release.set()
    thread.join(timeout=2)
    try:
        assert not thread.is_alive()
        assert errors == []
        assert manager._generate_ids()[0] == "task_00000003"
    finally:
        manager.close()
        parent.close()


def test_zero_timeout_is_an_immediate_poll(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)

    polled = manager.get_task(task.id, block=True, timeout=0)
    assert polled.status == TaskStatus.RUNNING
    conversation.finish()
    assert manager.get_task(task.id, block=True, timeout=2).status == (
        TaskStatus.COMPLETED
    )


def test_multiple_background_tasks_run_concurrently(task_runtime) -> None:
    manager, parent = task_runtime
    first_conversation = _ControlledConversation("first")
    second_conversation = _ControlledConversation("second")
    _install_conversations(manager, [first_conversation, second_conversation])

    first = _start_background(manager, parent, prompt="one")
    second = _start_background(manager, parent, prompt="two")
    assert first.id != second.id
    assert first_conversation.started.wait(timeout=2)
    assert second_conversation.started.wait(timeout=2)
    assert _worker_for(manager, first.id).is_alive()
    assert _worker_for(manager, second.id).is_alive()

    second_conversation.finish()
    first_conversation.finish()
    assert manager.get_task(first.id, block=True, timeout=2).result == "first"
    assert manager.get_task(second.id, block=True, timeout=2).result == "second"


def test_active_task_cannot_be_resumed_concurrently(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)

    with pytest.raises(ValueError, match="already active"):
        manager.start_task(
            prompt="conflicting continuation",
            subagent_type="test-agent",
            resume=task.id,
            conversation=parent,
            run_in_background=True,
        )

    assert manager.stop_task(task.id).status == TaskStatus.CANCELLED


def test_settled_background_task_resumes_with_same_id_and_fresh_completion(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    first_conversation = _ControlledConversation("first")
    resumed_conversation = _ControlledConversation("resumed")
    _install_conversations(manager, [first_conversation])

    first = _start_background(manager, parent)
    assert first_conversation.started.wait(timeout=2)
    first_conversation.finish()
    assert manager.get_task(first.id, block=True, timeout=2).result == "first"

    factory = SimpleNamespace(
        definition=SimpleNamespace(
            hooks=None,
            get_confirmation_policy=lambda: None,
        )
    )
    manager._get_sub_agent_from_factory = lambda _: parent.agent  # type: ignore[method-assign]
    manager._set_confirmation_policy = lambda *_: None  # type: ignore[method-assign]
    with patch(
        "openhands.tools.task.manager.get_agent_factory",
        return_value=factory,
    ):
        with patch(
            "openhands.tools.task.manager.LocalConversation",
            return_value=resumed_conversation,
        ):
            resumed = manager.start_task(
                prompt="continue",
                subagent_type="test-agent",
                resume=first.id,
                conversation=parent,
                run_in_background=True,
            )

    assert resumed.id == first.id
    assert resumed.status == TaskStatus.QUEUED
    assert resumed_conversation.started.wait(timeout=2)
    assert (
        manager.get_task(first.id, block=True, timeout=0).status == TaskStatus.RUNNING
    )
    resumed_conversation.finish()
    completed = manager.get_task(first.id, block=True, timeout=2)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.result == "resumed"


def test_terminal_task_cannot_be_resumed_before_settlement(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("completed result")
    _install_conversations(manager, [conversation])
    settlement_entered = threading.Event()
    release_settlement = threading.Event()
    original_settle = manager._settle_task

    def gated_settle(task: Task) -> None:
        settlement_entered.set()
        assert release_settlement.wait(timeout=2)
        original_settle(task)

    manager._settle_task = gated_settle  # type: ignore[method-assign]
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()
    assert settlement_entered.wait(timeout=2)
    try:
        with pytest.raises(ValueError, match="settling|active"):
            manager._resume_task(resume=task.id, subagent_type="default")
    finally:
        release_settlement.set()

    assert manager.get_task(task.id, block=True, timeout=2).status == (
        TaskStatus.COMPLETED
    )


def test_manager_rejects_a_second_parent(task_runtime, tmp_path: Path) -> None:
    manager, parent = task_runtime
    other_parent = _make_parent(tmp_path / "other")
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])
    try:
        with pytest.raises(RuntimeError, match="different parent"):
            manager.start_task(
                prompt="work",
                subagent_type="test-agent",
                conversation=other_parent,
                run_in_background=True,
            )
    finally:
        other_parent.close()

    assert manager.parent_conversation is parent


def test_closed_manager_rejects_parent_without_allocating_runtime(
    tmp_path: Path,
) -> None:
    manager = TaskManager()
    manager.close()
    parent = _make_parent(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="closed"):
            manager.start_task(
                prompt="must not allocate",
                conversation=parent,
                run_in_background=True,
            )
        assert manager._persistence_dir is None
    finally:
        if manager._persistence_dir is not None:
            shutil.rmtree(manager._persistence_dir, ignore_errors=True)
        parent.close()


def test_executor_close_serializes_concurrent_callers() -> None:
    manager = MagicMock(spec=TaskManager)
    close_entered = threading.Event()
    release_close = threading.Event()

    def blocked_close() -> None:
        close_entered.set()
        assert release_close.wait(timeout=2)

    manager.close.side_effect = blocked_close
    executor = TaskExecutor(manager)
    second_started = threading.Event()
    second_returned = threading.Event()

    first = threading.Thread(target=executor.close, name="first-executor-close")

    def second_close() -> None:
        second_started.set()
        executor.close()
        second_returned.set()

    second = threading.Thread(target=second_close, name="second-executor-close")
    first.start()
    assert close_entered.wait(timeout=2)
    second.start()
    assert second_started.wait(timeout=2)
    assert not second_returned.wait(timeout=0.1)
    release_close.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    manager.close.assert_called_once_with()


def test_concurrent_stop_requests_issue_one_interrupt(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(interrupt_releases=True)
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    barrier = threading.Barrier(3)
    results: list[Task] = []

    def stop() -> None:
        barrier.wait()
        results.append(manager.stop_task(task.id))

    first = threading.Thread(target=stop)
    second = threading.Thread(target=stop)
    first.start()
    second.start()
    barrier.wait()
    first.join(timeout=3)
    second.join(timeout=3)
    assert not first.is_alive()
    assert not second.is_alive()
    assert len(results) == 2
    assert all(result.status == TaskStatus.CANCELLED for result in results)
    assert conversation.interrupt_count == 1


def test_executor_close_allows_same_thread_reentry() -> None:
    manager = MagicMock(spec=TaskManager)
    executor = TaskExecutor(manager)
    manager.close.side_effect = executor.close

    executor.close()

    manager.close.assert_called_once_with()


def test_executor_close_can_retry_after_cleanup_failure() -> None:
    manager = MagicMock(spec=TaskManager)
    manager.close.side_effect = [RuntimeError("first close failed"), None]
    executor = TaskExecutor(manager)

    with pytest.raises(RuntimeError, match="first close failed"):
        executor.close()

    executor.close()

    assert manager.close.call_count == 2


def test_blocking_behavior_remains_the_default(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("blocking result")
    _install_conversations(manager, [conversation])

    task = manager.start_task(
        prompt="work",
        subagent_type="test-agent",
        conversation=parent,
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.result == "blocking result"
    assert conversation.sent_messages == [("work", None)]
    assert conversation.close_count == 1


def test_background_confirmation_handler_is_preserved(task_runtime) -> None:
    _, parent = task_runtime
    approvals: list[tuple[str, list[Any]]] = []

    def confirmation_handler(task_id: str, actions: list[Any]) -> bool:
        approvals.append((task_id, actions))
        return True

    manager = TaskManager(confirmation_handler=confirmation_handler)
    manager.attach_parent(parent)
    conversation = _ConfirmationConversation()
    _install_conversations(manager, [conversation])
    pending = [MagicMock()]
    try:
        with patch.object(
            manager_module_conversation_state(),
            "get_unmatched_actions",
            return_value=pending,
        ):
            task = _start_background(manager, parent)
            completed = manager.get_task(task.id, block=True, timeout=2)
        assert completed.status == TaskStatus.COMPLETED
        assert conversation.run_count == 2
        assert approvals == [(task.id, pending)]
    finally:
        manager.close()


def test_stop_during_confirmation_does_not_start_another_run(task_runtime) -> None:
    _, parent = task_runtime
    callback_entered = threading.Event()
    release_callback = threading.Event()

    def confirmation_handler(_task_id: str, _actions: list[Any]) -> bool:
        callback_entered.set()
        assert release_callback.wait(timeout=2)
        return True

    manager = TaskManager(confirmation_handler=confirmation_handler)
    manager.attach_parent(parent)
    conversation = _ConfirmationConversation()
    _install_conversations(manager, [conversation])
    pending = [MagicMock()]
    stop_thread: threading.Thread | None = None
    try:
        with patch.object(
            manager_module_conversation_state(),
            "get_unmatched_actions",
            return_value=pending,
        ):
            task = _start_background(manager, parent)
            assert callback_entered.wait(timeout=2)
            stop_thread = threading.Thread(target=lambda: manager.stop_task(task.id))
            stop_thread.start()
            assert conversation.interrupted.wait(timeout=2)
            release_callback.set()
            stop_thread.join(timeout=3)
            assert not stop_thread.is_alive()
            assert manager.get_task(task.id, block=True, timeout=2).status == (
                TaskStatus.CANCELLED
            )

        assert conversation.run_count == 1
    finally:
        release_callback.set()
        if stop_thread is not None:
            stop_thread.join(timeout=3)
        manager.close()


def manager_module_conversation_state():
    """Keep the patch target explicit without replacing the state class."""
    from openhands.tools.task import manager as manager_module

    return manager_module.ConversationState


def test_metrics_settle_once_and_parent_scoped_ids_do_not_collide(
    tmp_path: Path,
) -> None:
    parent = _make_parent(tmp_path)
    first_manager = TaskManager()
    second_manager = TaskManager()
    first_manager.attach_parent(parent)
    second_manager.attach_parent(parent)
    first_metrics = Metrics()
    second_metrics = Metrics()
    first_metrics.add_cost(1.0)
    second_metrics.add_cost(2.0)
    first_conversation = _ControlledConversation("first", metrics=first_metrics)
    second_conversation = _ControlledConversation("second", metrics=second_metrics)
    _install_conversations(first_manager, [first_conversation])
    _install_conversations(second_manager, [second_conversation])
    try:
        first = _start_background(first_manager, parent)
        second = _start_background(second_manager, parent)
        assert first.id != second.id
        assert first_conversation.started.wait(timeout=2)
        assert second_conversation.started.wait(timeout=2)
        first_conversation.finish()
        second_conversation.finish()
        assert first_manager.get_task(first.id, block=True, timeout=2).result == "first"
        assert (
            second_manager.get_task(second.id, block=True, timeout=2).result == "second"
        )

        for _ in range(3):
            first_manager.get_task(first.id)
            second_manager.get_task(second.id)
        first_manager._settle_task(first_manager._tasks[first.id])
        second_manager._settle_task(second_manager._tasks[second.id])

        usage = parent.conversation_stats.usage_to_metrics
        assert usage[f"task:{first.id}"].accumulated_cost == 1.0
        assert usage[f"task:{second.id}"].accumulated_cost == 2.0
        first_conversation.conversation_stats.get_combined_metrics.assert_called_once()
        second_conversation.conversation_stats.get_combined_metrics.assert_called_once()
    finally:
        first_manager.close()
        second_manager.close()
        parent.close()


def test_metrics_base_exception_does_not_block_terminal_settlement(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("metric result")
    _install_conversations(manager, [conversation])
    manager._update_parent_metrics = MagicMock(side_effect=KeyboardInterrupt())

    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()

    completed = manager.get_task(task.id, block=True, timeout=2)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.settled is True
    assert conversation.close_count == 1
    manager._update_parent_metrics.assert_called_once()


def test_interrupt_base_exception_does_not_abort_manager_close(task_runtime) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(interrupt_releases=True)
    _install_conversations(manager, [conversation])
    _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)

    def fail_interrupt() -> None:
        conversation.finish()
        raise _ProbeBaseException("interrupt failed")

    conversation.interrupt = fail_interrupt  # type: ignore[method-assign]
    manager.close()

    assert manager._cleanup_complete is True
    assert manager._tasks == {}


def test_response_extraction_base_exception_is_terminal_and_contained(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("result")
    _install_conversations(manager, [conversation])

    with patch(
        "openhands.tools.task.manager.get_agent_final_response",
        side_effect=_ProbeBaseException("extract failed"),
    ):
        task = _start_background(manager, parent)
        assert conversation.started.wait(timeout=2)
        conversation.finish()
        settled = manager.get_task(task.id, block=True, timeout=2)

    assert settled.status == TaskStatus.ERROR
    assert settled.settled is True
    assert settled.error is not None
    assert "extract failed" in settled.error
    assert not _worker_for(manager, task.id).is_alive()


def test_cleanup_base_exception_does_not_leave_conversation_attached(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("cleanup result")
    _install_conversations(manager, [conversation])

    def fail_close() -> None:
        conversation.close_count += 1
        raise SystemExit(2)

    conversation.close = fail_close  # type: ignore[method-assign]
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()

    completed = manager.get_task(task.id, block=True, timeout=2)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.settled is True
    assert manager._tasks[task.id].conversation is None
    assert conversation.close_count == 1


def test_background_metric_registration_does_not_mutate_parent_dict_during_iteration(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation("metric result")
    _install_conversations(manager, [conversation])
    entered = threading.Event()
    release = threading.Event()
    assignment_started = threading.Event()

    class BlockingMetricsDict(dict[str, Metrics]):
        def values(self):  # type: ignore[override]
            iterator = iter(super().values())
            entered.set()
            assert release.wait(timeout=2)
            return iterator

    parent.conversation_stats.usage_to_metrics = BlockingMetricsDict()
    original_update = manager._update_parent_metrics

    def tracked_update(parent_conversation: LocalConversation, task: Task) -> None:
        assignment_started.set()
        original_update(parent_conversation, task)

    manager._update_parent_metrics = tracked_update  # type: ignore[method-assign]
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)

    reader_errors: list[BaseException] = []

    def read_metrics() -> None:
        try:
            parent.conversation_stats.get_combined_metrics()
        except BaseException as error:  # pragma: no cover - assertion below
            reader_errors.append(error)

    reader = threading.Thread(target=read_metrics, name="metrics-reader")
    reader.start()
    assert entered.wait(timeout=2)
    conversation.finish()
    assert assignment_started.wait(timeout=2)
    release.set()
    reader.join(timeout=2)
    assert not reader.is_alive()
    assert reader_errors == []
    assert manager.get_task(task.id, block=True, timeout=2).status == (
        TaskStatus.COMPLETED
    )


def test_partial_thread_start_failure_settles_without_worker_leak(
    task_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])
    original_start = threading.Thread.start

    def start_then_fail(thread: threading.Thread) -> None:
        original_start(thread)
        raise RuntimeError("partial start failure")

    monkeypatch.setattr(threading.Thread, "start", start_then_fail)
    task = _start_background(manager, parent)
    worker = _worker_for(manager, task.id)

    assert task.status == TaskStatus.ERROR
    assert task.error is not None
    assert "partial start failure" in task.error
    assert not worker.is_alive()
    assert conversation.close_count == 1


def test_base_exception_during_thread_start_rolls_back_without_leak(
    task_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])

    def fail_start(_thread: threading.Thread) -> None:
        raise _ProbeBaseException("base start failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    task = _start_background(manager, parent)

    assert task.status == TaskStatus.ERROR
    assert task.error is not None and "base start failure" in task.error
    assert task.settled is True
    assert conversation.close_count == 1
    assert not manager._tasks[task.id].conversation


def test_task_setup_failure_rolls_back_unpublished_conversation(
    tmp_path: Path,
) -> None:
    parent = _make_parent(tmp_path)
    manager = TaskManager()
    manager.attach_parent(parent)
    child = LocalConversation(
        agent=parent.agent,
        workspace=str(tmp_path),
        delete_on_close=False,
    )
    child_close = MagicMock(wraps=child.close)
    child.close = child_close  # type: ignore[method-assign]
    factory = SimpleNamespace(
        definition=SimpleNamespace(
            max_iteration_per_run=1,
            max_budget_per_run=None,
            hooks=None,
            get_confirmation_policy=lambda: None,
        )
    )
    manager._get_sub_agent_from_factory = lambda _: parent.agent  # type: ignore[method-assign]
    manager._get_conversation = lambda **_: child  # type: ignore[method-assign]
    manager._set_confirmation_policy = lambda *_: None  # type: ignore[method-assign]
    manager._reserve_parent_metrics = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("metrics reservation failed")
    )
    try:
        with patch(
            "openhands.tools.task.manager.get_agent_factory",
            return_value=factory,
        ):
            with pytest.raises(RuntimeError, match="metrics reservation failed"):
                manager.start_task(
                    prompt="work",
                    subagent_type="test-agent",
                    conversation=parent,
                    run_in_background=True,
                )
        assert manager._tasks == {}
        assert parent.conversation_stats.usage_to_metrics == {}
        child_close.assert_called_once_with()
    finally:
        manager.close()
        parent.close()


def test_resume_setup_failure_closes_unpublished_conversation(
    tmp_path: Path,
) -> None:
    parent = _make_parent(tmp_path)
    manager = TaskManager()
    manager.attach_parent(parent)
    archived = Task(
        id="task_00000001",
        conversation_id=uuid.uuid4(),
        status=TaskStatus.COMPLETED,
        settled=True,
    )
    manager._tasks[archived.id] = archived
    conversation = _ControlledConversation()
    factory = SimpleNamespace(
        definition=SimpleNamespace(
            hooks=None,
            get_confirmation_policy=lambda: None,
        )
    )
    manager._get_sub_agent_from_factory = lambda _: parent.agent  # type: ignore[method-assign]

    def fail_policy(*_: Any) -> None:
        raise RuntimeError("policy setup failed")

    manager._set_confirmation_policy = fail_policy  # type: ignore[method-assign]
    try:
        with patch(
            "openhands.tools.task.manager.get_agent_factory",
            return_value=factory,
        ):
            with patch(
                "openhands.tools.task.manager.LocalConversation",
                return_value=conversation,
            ):
                with pytest.raises(RuntimeError, match="policy setup failed"):
                    manager._resume_task(
                        resume=archived.id,
                        subagent_type="test-agent",
                    )
        assert conversation.close_count == 1
        assert manager._tasks[archived.id].conversation is None
        assert manager._tasks[archived.id].settled is True
    finally:
        manager.close()
        parent.close()


def test_close_interrupts_tasks_joins_workers_and_removes_temp_dir(
    task_runtime,
) -> None:
    manager, parent = task_runtime
    first_conversation = _ControlledConversation()
    second_conversation = _ControlledConversation()
    _install_conversations(manager, [first_conversation, second_conversation])
    first = _start_background(manager, parent)
    second = _start_background(manager, parent)
    assert first_conversation.started.wait(timeout=2)
    assert second_conversation.started.wait(timeout=2)
    workers = [_worker_for(manager, first.id), _worker_for(manager, second.id)]
    persistence_dir = manager._persistence_dir
    assert persistence_dir is not None and persistence_dir.exists()

    manager.close()

    assert first_conversation.interrupted.is_set()
    assert second_conversation.interrupted.is_set()
    assert all(not worker.is_alive() for worker in workers)
    assert manager._cleanup_complete is True
    assert manager._tasks == {}
    assert not persistence_dir.exists()


def test_close_defers_destructive_cleanup_until_uncooperative_worker_exits(
    task_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, parent = task_runtime
    conversation = _ControlledConversation(interrupt_releases=False)
    _install_conversations(manager, [conversation])
    task = _start_background(manager, parent)
    assert conversation.started.wait(timeout=2)
    worker = _worker_for(manager, task.id)
    monkeypatch.setattr(
        "openhands.tools.task.manager._TASK_STOP_TIMEOUT_SECONDS",
        0.0,
    )

    manager.close()

    assert worker.is_alive()
    assert manager._cleanup_complete is False
    assert task.id in manager._tasks
    conversation.finish()
    assert conversation.closed.wait(timeout=2)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert manager._cleanup_complete is True
    assert manager._tasks == {}


def test_parent_conversation_close_reaches_shared_task_executor(tmp_path: Path) -> None:
    parent = _make_parent(tmp_path, tools=[Tool(name=TaskToolSet.name)])
    parent._ensure_agent_ready()
    task_tool = parent.agent.tools_map["task"].as_executable()
    executor = cast(TaskExecutor, task_tool.executor)
    manager = executor._manager
    conversation = _ControlledConversation()
    _install_conversations(manager, [conversation])

    started = executor(
        TaskAction(prompt="work", run_in_background=True),
        conversation=parent,
    )
    assert conversation.started.wait(timeout=2)
    worker = _worker_for(manager, started.task_id)

    parent.close()

    assert conversation.interrupted.is_set()
    assert not worker.is_alive()
    assert manager._cleanup_complete is True


def test_new_manager_does_not_restore_live_registry_from_persistence(
    tmp_path: Path,
) -> None:
    persistence_dir = tmp_path / "persisted"
    first_parent = _make_parent(tmp_path, persistence_dir=persistence_dir)
    first_manager = TaskManager()
    first_manager.attach_parent(first_parent)
    conversation = _ControlledConversation("persisted result")
    _install_conversations(first_manager, [conversation])
    first = _start_background(first_manager, first_parent)
    assert conversation.started.wait(timeout=2)
    conversation.finish()
    assert first_manager.get_task(first.id, block=True, timeout=2).status == (
        TaskStatus.COMPLETED
    )
    first_manager.close()
    first_parent.close()

    restarted_parent = _make_parent(tmp_path, persistence_dir=persistence_dir)
    restarted_manager = TaskManager()
    restarted_manager.attach_parent(restarted_parent)
    try:
        with pytest.raises(ValueError, match="not found"):
            restarted_manager.get_task(first.id)
        with pytest.raises(ValueError, match="not found"):
            restarted_manager.start_task(
                prompt="resume",
                subagent_type="test-agent",
                resume=first.id,
                conversation=restarted_parent,
            )
    finally:
        restarted_manager.close()
        restarted_parent.close()


def _make_llm_response() -> LLMResponse:
    return LLMResponse(
        message=Message(
            role="assistant",
            content=[TextContent(text="unused")],
        ),
        metrics=MetricsSnapshot(
            model_name="event-llm",
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(model="event-llm"),
        ),
        raw_response=MagicMock(spec=ModelResponse, id="response-1"),
    )


class _EventLLM(LLM):
    """Real LocalConversation LLM that waits until arun is cancelled."""

    _started: threading.Event = PrivateAttr(default_factory=threading.Event)

    def __init__(self) -> None:
        super().__init__(model="event-llm", usage_id="event-llm")

    def completion(self, messages, tools=None, **kwargs) -> LLMResponse:  # type: ignore[override]  # noqa: ARG002
        raise AssertionError("Background tasks must use the async conversation path")

    async def acompletion(self, messages, tools=None, **kwargs) -> LLMResponse:  # type: ignore[override]  # noqa: ARG002
        self._started.set()
        await asyncio.Event().wait()
        return _make_llm_response()


def test_stop_uses_real_local_conversation_interrupt(tmp_path: Path) -> None:
    _reset_registry_for_tests()
    event_llm = _EventLLM()
    register_agent(
        name="event-agent",
        factory_func=lambda llm: Agent(llm=event_llm, tools=[]),
        description="Event-driven test agent",
    )
    parent = _make_parent(tmp_path)
    manager = TaskManager()
    manager.attach_parent(parent)
    try:
        task = manager.start_task(
            prompt="wait for cancellation",
            subagent_type="event-agent",
            conversation=parent,
            run_in_background=True,
        )
        sub_conversation = manager._tasks[task.id].conversation
        assert sub_conversation is not None
        assert event_llm._started.wait(timeout=5)

        cancelled = manager.stop_task(task.id)

        assert cancelled.status == TaskStatus.CANCELLED
        assert any(
            isinstance(event, InterruptEvent) for event in sub_conversation.state.events
        )
        assert not _worker_for(manager, task.id).is_alive()
    finally:
        manager.close()
        parent.close()
        _reset_registry_for_tests()
