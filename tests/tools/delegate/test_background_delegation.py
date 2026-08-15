"""Tests for non-blocking delegate task lifecycles."""

import asyncio
import threading
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic import PrivateAttr

from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import LLMResponse
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.tools.delegate import (
    DelegateExecutor,
    DelegateObservation,
    DelegateTaskStatus,
)
from openhands.tools.delegate.definition import CommandLiteral, DelegateAction


class ControlledConversation:
    """Small async conversation double controlled by thread-safe events."""

    def __init__(self, blocking_result: str = "blocking result") -> None:
        self.state = SimpleNamespace(
            execution_status=ConversationExecutionStatus.IDLE,
            events=[],
        )
        self.started = threading.Event()
        self.interrupted = threading.Event()
        self.closed = threading.Event()
        self.messages: list[tuple[str, str | None]] = []
        self.blocking_result = blocking_result
        self.metrics = Metrics()
        self.conversation_stats = MagicMock()
        self.conversation_stats.get_combined_metrics.return_value = self.metrics

        self._control_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event: asyncio.Event | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._result: str | None = None
        self._error: Exception | None = None
        self._completion_status = ConversationExecutionStatus.FINISHED

    def send_message(self, message: str, sender: str | None = None) -> None:
        self.messages.append((message, sender))

    def run(self) -> None:
        self.state.events.append(self._message_event(self.blocking_result))
        self.state.execution_status = ConversationExecutionStatus.FINISHED

    async def arun(self) -> None:
        loop = asyncio.get_running_loop()
        wake_event = asyncio.Event()
        current_task = asyncio.current_task()
        assert current_task is not None

        with self._control_lock:
            self._loop = loop
            self._wake_event = wake_event
            self._run_task = current_task
            self.state.execution_status = ConversationExecutionStatus.RUNNING
            self.started.set()

        try:
            await wake_event.wait()
            with self._control_lock:
                result = self._result
                error = self._error
            if error is not None:
                self.state.execution_status = ConversationExecutionStatus.ERROR
                raise error
            assert result is not None
            self.state.events.append(self._message_event(result))
            self.state.execution_status = self._completion_status
        except asyncio.CancelledError:
            self.state.execution_status = ConversationExecutionStatus.PAUSED
        finally:
            with self._control_lock:
                self._run_task = None

    def complete(self, result: str) -> None:
        with self._control_lock:
            self._result = result
            loop = self._loop
            wake_event = self._wake_event
        assert loop is not None
        assert wake_event is not None
        loop.call_soon_threadsafe(wake_event.set)

    def fail(self, error: Exception) -> None:
        with self._control_lock:
            self._error = error
            loop = self._loop
            wake_event = self._wake_event
        assert loop is not None
        assert wake_event is not None
        loop.call_soon_threadsafe(wake_event.set)

    def stop_without_finishing(
        self,
        result: str,
        status: ConversationExecutionStatus,
    ) -> None:
        with self._control_lock:
            self._result = result
            self._completion_status = status
            loop = self._loop
            wake_event = self._wake_event
        assert loop is not None
        assert wake_event is not None
        loop.call_soon_threadsafe(wake_event.set)

    def interrupt(self) -> None:
        self.interrupted.set()
        with self._control_lock:
            loop = self._loop
            task = self._run_task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        else:
            self.state.execution_status = ConversationExecutionStatus.PAUSED

    def close(self) -> None:
        self.closed.set()

    @staticmethod
    def _message_event(text: str) -> MessageEvent:
        return MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text=text)],
            ),
        )


class MessageGateConversation(ControlledConversation):
    """Hold a worker between send_message() and arun()."""

    def __init__(self) -> None:
        super().__init__()
        self.message_started = threading.Event()
        self.allow_message = threading.Event()

    def send_message(self, message: str, sender: str | None = None) -> None:
        super().send_message(message, sender)
        self.message_started.set()
        assert self.allow_message.wait(timeout=5)


class ConfirmationConversation(ControlledConversation):
    """Return one confirmation boundary before completing."""

    def __init__(self) -> None:
        super().__init__()
        self.arun_calls = 0

    async def arun(self) -> None:
        self.arun_calls += 1
        self.started.set()
        if self.arun_calls == 1:
            self.state.execution_status = (
                ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            )
            return
        self.state.events.append(self._message_event("confirmed result"))
        self.state.execution_status = ConversationExecutionStatus.FINISHED


class UnexpectedCancellationConversation(ControlledConversation):
    """Surface an unexpected cancellation instead of consuming it."""

    async def arun(self) -> None:
        self.started.set()
        raise asyncio.CancelledError


class InterruptibleLLM(LLM):
    """Real LocalConversation LLM that only exits through cancellation."""

    _started: threading.Event = PrivateAttr(default_factory=threading.Event)

    def __init__(self) -> None:
        super().__init__(model="test-interruptible", usage_id="test-interruptible")

    def completion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args, kwargs
        raise AssertionError("background delegation must use LocalConversation.arun()")

    async def acompletion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args, kwargs
        self._started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _parent() -> MagicMock:
    parent = MagicMock(spec=LocalConversation)
    parent.id = uuid.uuid4()
    parent._visualizer = None
    parent.conversation_stats = ConversationStats()
    return parent


def _executor_with_agents(
    **agents: ControlledConversation | LocalConversation,
) -> DelegateExecutor:
    executor = DelegateExecutor()
    executor._sub_agents.update(
        {
            agent_id: cast(LocalConversation, conversation)
            for agent_id, conversation in agents.items()
        }
    )
    return executor


def _task_id(observation, agent_id: str) -> str:
    assert observation.is_error is False
    assert observation.task_ids is not None
    return observation.task_ids[agent_id]


def _join_background_task(executor: DelegateExecutor, task_id: str) -> None:
    record = executor._background_tasks[task_id]
    assert record.thread is not None
    record.thread.join(timeout=2)
    assert not record.thread.is_alive()


def test_delegate_task_status_state_machine() -> None:
    assert [status.value for status in DelegateTaskStatus] == [
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    ]


def test_background_returns_before_completion_and_output_is_idempotent() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()
    returned = threading.Event()
    result: dict[str, DelegateObservation] = {}

    def invoke() -> None:
        result["observation"] = executor(
            DelegateAction(
                command="delegate",
                tasks={"worker": "do the work"},
                background=True,
            ),
            parent,
        )
        returned.set()

    caller = threading.Thread(target=invoke)
    caller.start()
    if not returned.wait(timeout=2):
        if conversation.started.wait(timeout=2):
            conversation.complete("release blocked call")
        caller.join(timeout=2)
        pytest.fail("background delegation blocked until the sub-agent completed")
    caller.join(timeout=2)

    observation = result["observation"]
    task_id = _task_id(observation, "worker")
    assert conversation.started.wait(timeout=2)

    status = executor(
        DelegateAction(command="status", task_id=task_id),
        parent,
    )
    assert status.status == DelegateTaskStatus.RUNNING
    assert status.agent_id == "worker"

    pending_output = executor(
        DelegateAction(command="output", task_id=task_id),
        parent,
    )
    assert pending_output.is_error is True
    assert pending_output.status == DelegateTaskStatus.RUNNING
    assert "not finished" in pending_output.text.lower()

    conversation.complete("finished output")
    _join_background_task(executor, task_id)

    completed = executor(
        DelegateAction(command="status", task_id=task_id),
        parent,
    )
    first_output = executor(
        DelegateAction(command="output", task_id=task_id),
        parent,
    )
    second_output = executor(
        DelegateAction(command="output", task_id=task_id),
        parent,
    )

    assert completed.status == DelegateTaskStatus.COMPLETED
    assert first_output.text == "finished output"
    assert second_output.text == first_output.text
    assert first_output.status == DelegateTaskStatus.COMPLETED
    conversation.conversation_stats.get_combined_metrics.assert_called_once_with()
    assert (
        parent.conversation_stats.usage_to_metrics["delegate:worker"]
        is conversation.metrics
    )

    executor.close()


def test_background_failure_is_stable_across_polls() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "fail"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert conversation.started.wait(timeout=2)
    conversation.fail(RuntimeError("controlled failure"))
    _join_background_task(executor, task_id)

    status = executor(DelegateAction(command="status", task_id=task_id), parent)
    first_output = executor(DelegateAction(command="output", task_id=task_id), parent)
    second_output = executor(DelegateAction(command="output", task_id=task_id), parent)

    assert status.status == DelegateTaskStatus.FAILED
    assert first_output.is_error is True
    assert first_output.status == DelegateTaskStatus.FAILED
    assert "controlled failure" in first_output.text
    assert second_output.text == first_output.text
    conversation.conversation_stats.get_combined_metrics.assert_called_once_with()

    executor.close()


def test_unexpected_background_cancellation_fails_and_releases_agent() -> None:
    conversation = UnexpectedCancellationConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "cancel unexpectedly"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    _join_background_task(executor, task_id)

    status = executor(DelegateAction(command="status", task_id=task_id), parent)
    output = executor(DelegateAction(command="output", task_id=task_id), parent)

    assert status.status == DelegateTaskStatus.FAILED
    assert output.status == DelegateTaskStatus.FAILED
    assert output.is_error is True
    assert "cancelled unexpectedly" in output.text
    assert executor._active_agents.get("worker") is None
    conversation.conversation_stats.get_combined_metrics.assert_called_once_with()
    executor.close()


def test_background_result_extraction_failure_settles_failed() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "fail while extracting output"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert conversation.started.wait(timeout=2)

    with patch(
        "openhands.tools.delegate.impl.get_agent_final_response",
        side_effect=RuntimeError("result extraction failed"),
    ):
        conversation.complete("result")
        _join_background_task(executor, task_id)

    status = executor(DelegateAction(command="status", task_id=task_id), parent)
    output = executor(DelegateAction(command="output", task_id=task_id), parent)
    assert status.status == DelegateTaskStatus.FAILED
    assert output.status == DelegateTaskStatus.FAILED
    assert output.is_error is True
    assert "result extraction failed" in output.text
    assert executor._active_agents.get("worker") is None
    conversation.conversation_stats.get_combined_metrics.assert_called_once_with()
    executor.close()


def test_non_finished_result_and_metrics_settle_once() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "pause with partial output"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert conversation.started.wait(timeout=2)

    with patch(
        "openhands.tools.delegate.impl.get_agent_final_response",
        wraps=get_agent_final_response,
    ) as extract_response:
        conversation.stop_without_finishing(
            "partial output",
            ConversationExecutionStatus.PAUSED,
        )
        _join_background_task(executor, task_id)

    output = executor(DelegateAction(command="output", task_id=task_id), parent)
    assert output.status == DelegateTaskStatus.FAILED
    assert "partial output" in output.text
    extract_response.assert_called_once_with(conversation.state.events)
    conversation.conversation_stats.get_combined_metrics.assert_called_once_with()
    executor.close()


def test_background_preserves_confirmation_handler_contract() -> None:
    conversation = ConfirmationConversation()
    pending_action = cast(ActionEvent, MagicMock(spec=ActionEvent))
    handler_calls: list[tuple[str, list[ActionEvent]]] = []

    def confirmation_handler(agent_id: str, actions: list[ActionEvent]) -> bool:
        handler_calls.append((agent_id, actions))
        return True

    executor = DelegateExecutor(confirmation_handler=confirmation_handler)
    executor._sub_agents["worker"] = cast(LocalConversation, conversation)
    parent = _parent()

    with patch.object(
        ConversationState,
        "get_unmatched_actions",
        return_value=[pending_action],
    ):
        started = executor(
            DelegateAction(
                command="delegate",
                tasks={"worker": "confirm"},
                background=True,
            ),
            parent,
        )
        task_id = _task_id(started, "worker")
        _join_background_task(executor, task_id)

    status = executor(DelegateAction(command="status", task_id=task_id), parent)
    output = executor(DelegateAction(command="output", task_id=task_id), parent)
    assert handler_calls == [("worker", [pending_action])]
    assert conversation.arun_calls == 2
    assert status.status == DelegateTaskStatus.COMPLETED
    assert output.text == "confirmed result"
    executor.close()


def test_stop_is_cooperative_and_repeated_stop_is_idempotent() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "wait"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert conversation.started.wait(timeout=2)

    stopped = executor(DelegateAction(command="stop", task_id=task_id), parent)
    stopped_again = executor(DelegateAction(command="stop", task_id=task_id), parent)
    output = executor(DelegateAction(command="output", task_id=task_id), parent)

    assert conversation.interrupted.is_set()
    assert stopped.status == DelegateTaskStatus.CANCELLED
    assert stopped_again.status == DelegateTaskStatus.CANCELLED
    assert stopped_again.is_error is False
    assert "already cancelled" in stopped_again.text.lower()
    assert output.status == DelegateTaskStatus.CANCELLED
    assert output.is_error is True
    _join_background_task(executor, task_id)

    executor.close()


def test_stop_during_message_delivery_cancels_when_arun_starts() -> None:
    conversation = MessageGateConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "wait"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert conversation.message_started.wait(timeout=2)

    stop_returned = threading.Event()
    result: dict[str, DelegateObservation] = {}

    def stop() -> None:
        result["observation"] = executor(
            DelegateAction(command="stop", task_id=task_id), parent
        )
        stop_returned.set()

    stop_thread = threading.Thread(target=stop)
    stop_thread.start()
    try:
        assert conversation.interrupted.wait(timeout=2)
    finally:
        conversation.allow_message.set()

    assert stop_returned.wait(timeout=2)
    stop_thread.join(timeout=2)
    assert not stop_thread.is_alive()
    assert result["observation"].status == DelegateTaskStatus.CANCELLED
    _join_background_task(executor, task_id)
    executor.close()


def test_multiple_background_agents_run_concurrently_and_close_cleans_workers() -> None:
    first = ControlledConversation()
    second = ControlledConversation()
    executor = _executor_with_agents(first=first, second=second)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"first": "one", "second": "two"},
            background=True,
        ),
        parent,
    )
    assert started.task_ids is not None
    assert set(started.task_ids) == {"first", "second"}
    assert first.started.wait(timeout=2)
    assert second.started.wait(timeout=2)
    records = [
        executor._background_tasks[task_id] for task_id in started.task_ids.values()
    ]

    executor.close()

    assert first.interrupted.is_set()
    assert second.interrupted.is_set()
    assert first.closed.is_set()
    assert second.closed.is_set()
    assert all(record.thread is not None for record in records)
    assert all(not record.thread.is_alive() for record in records if record.thread)
    assert executor._background_tasks == {}
    assert executor._sub_agents == {}


def test_close_waits_for_terminal_worker_to_leave_settlement() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()
    settled = threading.Event()
    release_worker = threading.Event()
    join_called = threading.Event()
    original_settle = executor._settle_background_task

    def hold_worker_after_settlement(*args: Any, **kwargs: Any) -> None:
        original_settle(*args, **kwargs)
        settled.set()
        assert release_worker.wait(timeout=5)

    with patch.object(
        executor, "_settle_background_task", hold_worker_after_settlement
    ):
        started = executor(
            DelegateAction(
                command="delegate",
                tasks={"worker": "finish before close"},
                background=True,
            ),
            parent,
        )
        task_id = _task_id(started, "worker")
        assert conversation.started.wait(timeout=2)
        record = executor._background_tasks[task_id]
        assert record.thread is not None
        original_join = record.thread.join

        def observe_join(timeout: float | None = None) -> None:
            join_called.set()
            original_join(timeout=timeout)

        conversation.complete("done")
        assert settled.wait(timeout=2)
        status = executor(
            DelegateAction(command="status", task_id=task_id),
            parent,
        )
        assert status.status == DelegateTaskStatus.COMPLETED

        close_thread = threading.Thread(target=executor.close)
        with patch.object(record.thread, "join", observe_join):
            close_thread.start()
            try:
                assert join_called.wait(timeout=2)
            finally:
                release_worker.set()
            close_thread.join(timeout=2)

        assert not close_thread.is_alive()
        assert not record.thread.is_alive()
        assert executor._background_tasks == {}


def test_background_thread_constructor_failure_is_recorded_and_released() -> None:
    first = ControlledConversation()
    second = ControlledConversation()
    executor = _executor_with_agents(first=first, second=second)
    parent = _parent()
    original_thread = threading.Thread

    def fail_second_constructor(*args: Any, **kwargs: Any) -> threading.Thread:
        if kwargs.get("name", "").startswith("Delegate-second-"):
            raise RuntimeError("injected thread constructor failure")
        return original_thread(*args, **kwargs)

    with patch(
        "openhands.tools.delegate.impl.threading.Thread",
        side_effect=fail_second_constructor,
    ):
        started = executor(
            DelegateAction(
                command="delegate",
                tasks={"first": "run", "second": "fail to construct"},
                background=True,
            ),
            parent,
        )

    assert started.is_error is True
    assert started.task_ids is not None
    first_id = started.task_ids["first"]
    second_id = started.task_ids["second"]
    assert first.started.wait(timeout=2)
    first.complete("done")
    _join_background_task(executor, first_id)

    second_status = executor(
        DelegateAction(command="status", task_id=second_id),
        parent,
    )
    second_output = executor(
        DelegateAction(command="output", task_id=second_id),
        parent,
    )
    assert second_status.status == DelegateTaskStatus.FAILED
    assert second_output.status == DelegateTaskStatus.FAILED
    assert "constructor failure" in second_output.text
    assert executor._active_agents == {}
    assert second.conversation_stats.get_combined_metrics.call_count == 0
    executor.close()


def test_same_agent_rejects_overlapping_and_allows_later_unique_task() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    first = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "first"},
            background=True,
        ),
        parent,
    )
    first_task_id = _task_id(first, "worker")
    assert conversation.started.wait(timeout=2)

    overlapping_background = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "second"},
            background=True,
        ),
        parent,
    )
    overlapping_blocking = executor(
        DelegateAction(command="delegate", tasks={"worker": "blocking"}),
        parent,
    )
    replacing_spawn = executor(
        DelegateAction(command="spawn", ids=["worker"]),
        parent,
    )

    assert overlapping_background.is_error is True
    assert first_task_id in overlapping_background.text
    assert overlapping_blocking.is_error is True
    assert replacing_spawn.is_error is True

    conversation.complete("first result")
    _join_background_task(executor, first_task_id)

    conversation.started.clear()
    second = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "second"},
            background=True,
        ),
        parent,
    )
    second_task_id = _task_id(second, "worker")
    assert second_task_id != first_task_id
    assert conversation.started.wait(timeout=2)
    conversation.complete("second result")
    _join_background_task(executor, second_task_id)

    executor.close()


def test_concurrent_calls_reserve_one_task_per_agent() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()
    barrier = threading.Barrier(3, timeout=5)
    observations: list[DelegateObservation] = []
    observations_lock = threading.Lock()

    def launch(prompt: str) -> None:
        barrier.wait()
        observation = executor(
            DelegateAction(
                command="delegate",
                tasks={"worker": prompt},
                background=True,
            ),
            parent,
        )
        with observations_lock:
            observations.append(observation)

    callers = [
        threading.Thread(target=launch, args=("first",)),
        threading.Thread(target=launch, args=("second",)),
    ]
    for caller in callers:
        caller.start()
    barrier.wait()
    for caller in callers:
        caller.join(timeout=2)
        assert not caller.is_alive()

    successful = [
        observation for observation in observations if not observation.is_error
    ]
    rejected = [observation for observation in observations if observation.is_error]
    assert len(successful) == 1
    assert len(rejected) == 1
    task_id = _task_id(successful[0], "worker")
    assert task_id in rejected[0].text

    assert conversation.started.wait(timeout=2)
    conversation.complete("done")
    _join_background_task(executor, task_id)
    executor.close()


def test_blocking_delegate_remains_the_default() -> None:
    conversation = ControlledConversation(blocking_result="legacy result")
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    observation = executor(
        DelegateAction(command="delegate", tasks={"worker": "legacy"}),
        parent,
    )

    assert observation.is_error is False
    assert observation.task_ids is None
    assert "legacy result" in observation.text
    assert conversation.messages == [("legacy", None)]

    executor.close()


@pytest.mark.parametrize("command", ["status", "output", "stop"])
def test_lifecycle_commands_reject_unknown_or_missing_task_id(
    command: CommandLiteral,
) -> None:
    executor = DelegateExecutor()
    parent = _parent()

    missing = executor(DelegateAction(command=command), parent)
    unknown = executor(
        DelegateAction(command=command, task_id="delegate_missing"),
        parent,
    )

    assert missing.is_error is True
    assert "task_id is required" in missing.text
    assert unknown.is_error is True
    assert "not found" in unknown.text


def test_registry_is_bound_to_parent_and_is_not_restored() -> None:
    conversation = ControlledConversation()
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()
    other_parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "work"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert conversation.started.wait(timeout=2)

    wrong_parent = executor(
        DelegateAction(command="status", task_id=task_id),
        other_parent,
    )
    fresh_executor = DelegateExecutor()
    after_restart = fresh_executor(
        DelegateAction(command="status", task_id=task_id),
        parent,
    )

    assert wrong_parent.is_error is True
    assert "different parent" in wrong_parent.text.lower()
    assert after_restart.is_error is True
    assert "not found" in after_restart.text

    conversation.complete("done")
    _join_background_task(executor, task_id)
    executor.close()


def test_stop_uses_real_local_conversation_interrupt(tmp_path) -> None:
    llm = InterruptibleLLM()
    conversation = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
        persistence_dir=None,
    )
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    started = executor(
        DelegateAction(
            command="delegate",
            tasks={"worker": "wait for cancellation"},
            background=True,
        ),
        parent,
    )
    task_id = _task_id(started, "worker")
    assert llm._started.wait(timeout=5)

    stopped = executor(DelegateAction(command="stop", task_id=task_id), parent)

    assert stopped.status == DelegateTaskStatus.CANCELLED
    assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
    _join_background_task(executor, task_id)
    executor.close()


def test_stop_during_real_conversation_initialization_settles_cancelled(
    tmp_path,
) -> None:
    llm = InterruptibleLLM()
    conversation = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
        persistence_dir=None,
    )
    executor = _executor_with_agents(worker=conversation)
    parent = _parent()

    arun_init_started = threading.Event()
    release_arun_init = threading.Event()
    interrupt_called = threading.Event()
    ensure_lock = threading.Lock()
    ensure_calls = 0
    original_ensure_agent_ready = conversation._ensure_agent_ready
    original_interrupt = conversation.interrupt

    def block_arun_initialization() -> None:
        nonlocal ensure_calls
        with ensure_lock:
            ensure_calls += 1
            current_call = ensure_calls
        # send_message() initializes synchronously first. Hold the second call,
        # which LocalConversation.arun() dispatches through asyncio.to_thread().
        if current_call == 2:
            arun_init_started.set()
            assert release_arun_init.wait(timeout=5)
        original_ensure_agent_ready()

    def observe_interrupt() -> None:
        original_interrupt()
        interrupt_called.set()

    stop_result: dict[str, DelegateObservation] = {}
    stop_thread: threading.Thread | None = None
    try:
        with (
            patch.object(
                conversation,
                "_ensure_agent_ready",
                block_arun_initialization,
            ),
            patch.object(conversation, "interrupt", observe_interrupt),
        ):
            started = executor(
                DelegateAction(
                    command="delegate",
                    tasks={"worker": "cancel during initialization"},
                    background=True,
                ),
                parent,
            )
            task_id = _task_id(started, "worker")
            assert arun_init_started.wait(timeout=2)

            stop_thread = threading.Thread(
                target=lambda: stop_result.setdefault(
                    "observation",
                    executor(
                        DelegateAction(command="stop", task_id=task_id),
                        parent,
                    ),
                )
            )
            stop_thread.start()
            assert interrupt_called.wait(timeout=2)
            release_arun_init.set()
            stop_thread.join(timeout=2)
            assert not stop_thread.is_alive()

            _join_background_task(executor, task_id)
            final_status = executor(
                DelegateAction(command="status", task_id=task_id),
                parent,
            )
            reservation = executor._active_agents.get("worker")

        assert stop_result["observation"].status == DelegateTaskStatus.CANCELLED
        assert stop_result["observation"].is_error is False
        assert final_status.status == DelegateTaskStatus.CANCELLED
        assert reservation is None
    finally:
        release_arun_init.set()
        if stop_thread is not None:
            stop_thread.join(timeout=2)
        executor.close()
