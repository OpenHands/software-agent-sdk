"""Task lifecycle manager.

This module implements the core task orchestration layer.
The TaskManager class is responsible for creating, resuming,
and running sub-agent tasks. In other words, it handles
everything related to task management.

The conversation linked to a completed task is persisted in
a temporary directory, ensuring the state can be restored
if the task is resumed for further work later.
"""

import asyncio
import math
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.types import TraceMetadataValue
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import detached_delegate_context
from openhands.sdk.security import ConfirmationPolicyBase
from openhands.sdk.subagent.registry import AgentFactory, get_agent_factory


if TYPE_CHECKING:
    from openhands.sdk.event import ActionEvent

ConfirmationHandler = Callable[[str, list["ActionEvent"]], bool]


logger = get_logger(__name__)

_SUBAGENTS_DIR: Final[str] = "subagents"
_TASK_STOP_TIMEOUT_SECONDS: Final[float] = 5.0
_PARENT_TASK_RUNTIME_ATTR: Final[str] = "_openhands_task_runtime"
_PARENT_TASK_RUNTIME_LOCK = threading.Lock()


class _ParentTaskRuntime:
    """Process-local coordination shared by task managers for one parent."""

    def __init__(self, next_task_number: int = 1) -> None:
        self.lock = threading.Lock()
        self.next_task_number = next_task_number


def _get_parent_task_runtime(
    conversation: LocalConversation,
) -> _ParentTaskRuntime:
    with _PARENT_TASK_RUNTIME_LOCK:
        runtime = vars(conversation).get(_PARENT_TASK_RUNTIME_ATTR)
        if runtime is None:
            next_task_number = 1
            # Metrics are updated by worker threads. Snapshot the mapping before
            # scanning restored task IDs so the first parent bind cannot race a
            # concurrent settlement with a live dict iterator.
            conversation_stats = getattr(conversation, "conversation_stats", None)
            usage_to_metrics = getattr(conversation_stats, "usage_to_metrics", None)
            usage_ids = (
                tuple(usage_to_metrics.copy()) if usage_to_metrics is not None else ()
            )
            for usage_id in usage_ids:
                prefix = "task:task_"
                if not usage_id.startswith(prefix):
                    continue
                try:
                    next_task_number = max(
                        next_task_number,
                        int(usage_id[len(prefix) :], 16) + 1,
                    )
                except ValueError:
                    continue
            runtime = _ParentTaskRuntime(next_task_number=next_task_number)
            setattr(conversation, _PARENT_TASK_RUNTIME_ATTR, runtime)
        if not isinstance(runtime, _ParentTaskRuntime):
            raise RuntimeError("Parent conversation has invalid task runtime state.")
        return runtime


class TaskStatus(StrEnum):
    """Represents the lifecycle states of a task."""

    QUEUED = "queued"
    """The task has been accepted and its worker has not started yet."""

    RUNNING = "running"
    """The task is currently being processed by an agent."""

    COMPLETED = "completed"
    """The task completed successfully and returned a valid result or response."""

    ERROR = "error"
    """The task failed to complete due to an unhandled exception or system fault."""

    CANCELLED = "cancelled"
    """The task stopped after a cooperative cancellation request."""


_TERMINAL_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED}
)


class Task(BaseModel):
    """Represents a task."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(description="Unique identifier of the task.")
    status: TaskStatus = Field(description="Task status.")
    subagent: str = Field(
        default="default",
        description="Sub-agent type assigned to the task.",
    )
    conversation_id: uuid.UUID = Field(
        description="Conversation ID. Used to identify the conversation."
    )
    result: str | None = Field(default=None, description="Result of the task.")
    error: str | None = Field(default=None, description="Error if task failed.")
    conversation: LocalConversation | None = Field(
        default=None,
        exclude=True,
        description="Conversation state of the task.",
    )
    thread: threading.Thread | None = Field(default=None, exclude=True)
    completion_event: threading.Event = Field(
        default_factory=threading.Event,
        exclude=True,
    )
    # Signals lifecycle observers when a stop/close request arrives or the task
    # reaches settlement. This is intentionally separate from completion_event:
    # a blocking output read must not remain asleep while cooperative cleanup
    # is waiting on an unresponsive child conversation.
    wait_event: threading.Event = Field(
        default_factory=threading.Event,
        exclude=True,
    )
    stop_requested: bool = Field(default=False, exclude=True)
    metrics_settled: bool = Field(default=False, exclude=True)
    settlement_started: bool = Field(default=False, exclude=True)
    settled: bool = Field(default=False, exclude=True)

    def set_result(self, result: str | None) -> None:
        """Set task as successful."""
        self.result = result
        self.error = None
        self.status = TaskStatus.COMPLETED

    def set_error(self, error: str) -> None:
        """Set task as failed with an error."""
        self.error = error
        self.result = None
        self.status = TaskStatus.ERROR

    def set_cancelled(self, result: str | None = None) -> None:
        """Set task as cooperatively cancelled."""
        self.result = result
        self.error = None
        self.status = TaskStatus.CANCELLED


class TaskManager:
    """Manage sub-agent tasks."""

    def __init__(
        self,
        confirmation_handler: ConfirmationHandler | None = None,
    ):
        self._parent_conversation: LocalConversation | None = None
        self._confirmation_handler = confirmation_handler

        self._tasks: dict[str, Task] = {}
        self._tasks_lock = threading.RLock()
        self._next_task_number = 1
        self._parent_runtime: _ParentTaskRuntime | None = None
        self._closed = False
        self._cleanup_complete = False

        # Set once in _ensure_parent: uses the parent's subagents dir
        # when the parent persists, otherwise a temporary directory.
        self._persistence_dir: Path | None = None

    def attach_parent(self, conversation: LocalConversation) -> None:
        """Attach the parent conversation used to create sub-agent tasks.

        Idempotent: if a parent conversation is already attached, subsequent
        calls with the same conversation have no effect. Calls with a different
        conversation are also ignored, but log a warning to surface potential
        programming errors where two subsystems try to register different parents.
        """
        with self._tasks_lock:
            if self._closed:
                raise RuntimeError("Task manager is closed.")
            if (
                self._parent_conversation is not None
                and self._parent_conversation is not conversation
            ):
                logger.warning(
                    "attach_parent called with a different conversation; ignoring."
                )
            self._ensure_parent(conversation)

    def _ensure_parent(self, conversation: LocalConversation) -> None:
        with self._tasks_lock:
            if self._parent_conversation is None:
                self._parent_conversation = conversation
                self._parent_runtime = _get_parent_task_runtime(conversation)
                parent_persistence_dir = conversation.state.persistence_dir
                if parent_persistence_dir is not None:
                    self._persistence_dir = (
                        Path(parent_persistence_dir) / _SUBAGENTS_DIR
                    )
                    self._persistence_dir.mkdir(parents=True, exist_ok=True)
                else:
                    self._persistence_dir = Path(
                        tempfile.mkdtemp(prefix="openhands_tasks_")
                    )

    def _validate_parent(self, conversation: LocalConversation) -> None:
        """Reject lifecycle access from a different parent conversation."""
        with self._tasks_lock:
            if (
                self._parent_conversation is not None
                and self._parent_conversation is not conversation
            ):
                raise RuntimeError(
                    "Task manager is bound to a different parent conversation."
                )

    @property
    def parent_conversation(self) -> LocalConversation:
        if self._parent_conversation is None:
            raise RuntimeError(
                "Parent conversation not set. This should be set automatically "
                "on the first call to the executor."
            )
        return self._parent_conversation

    def _generate_ids(self) -> tuple[str, uuid.UUID]:
        """Generate a unique task ID, and a conversation ID."""
        runtime = self._parent_runtime
        if runtime is not None:
            with runtime.lock:
                task_number = runtime.next_task_number
                runtime.next_task_number += 1
        else:
            with self._tasks_lock:
                task_number = self._next_task_number
                self._next_task_number += 1
        return f"task_{task_number:08x}", uuid.uuid4()

    def _evict_task(self, task: Task) -> None:
        conversation = task.conversation
        if conversation is not None:
            try:
                conversation.pause()
            except BaseException as e:
                logger.warning("Failed to pause task '%s': %s", task.id, e)
            try:
                conversation.close()
            except BaseException as e:
                logger.warning("Failed to close task '%s': %s", task.id, e)
        with self._tasks_lock:
            archived = task.model_copy(
                update={
                    "conversation": None,
                    "settled": True,
                }
            )
            task.settled = True
            self._tasks[task.id] = archived

    @staticmethod
    def _close_unpublished_conversation(
        task_id: str,
        conversation: LocalConversation,
    ) -> None:
        """Release a conversation that never made it into the task registry."""
        try:
            conversation.close()
        except BaseException as e:
            logger.warning(
                "Failed to close unpublished task '%s' conversation: %s",
                task_id,
                e,
            )

    def start_task(
        self,
        prompt: str,
        subagent_type: str = "default",
        resume: str | None = None,
        description: str | None = None,
        conversation: LocalConversation | None = None,
        run_in_background: bool = False,
    ) -> Task:
        """Start a sub-agent task, blocking unless explicitly backgrounded.

        Args:
            prompt: The task description for the sub-agent.
            subagent_type: Type of agent to use.
            resume: Task ID to resume (continues existing conversation).
            description: Short label for the task.
            conversation: Parent conversation (set on first call).
            run_in_background: Return after starting a managed worker.

        Returns:
            TaskState with the final result.
        """
        background_result: Task | None = None
        failed_worker: threading.Thread | None = None
        with self._tasks_lock:
            if self._closed:
                raise RuntimeError("Task manager is closed.")
            if conversation:
                if (
                    self._parent_conversation is not None
                    and self._parent_conversation is not conversation
                ):
                    raise RuntimeError(
                        "Task manager is bound to a different parent conversation."
                    )
                self._ensure_parent(conversation)

            initial_status = (
                TaskStatus.QUEUED if run_in_background else TaskStatus.RUNNING
            )
            if resume:
                task = self._resume_task(
                    resume=resume,
                    subagent_type=subagent_type,
                    status=initial_status,
                )
            else:
                task = self._create_task(
                    subagent_type=subagent_type,
                    description=description,
                    status=initial_status,
                )

            if run_in_background:
                background_result = self._start_background_task(
                    task=task,
                    prompt=prompt,
                )
                if background_result.status == TaskStatus.ERROR:
                    failed_worker = task.thread
            else:
                task.thread = threading.current_thread()

        if background_result is not None:
            if (
                failed_worker is not None
                and failed_worker.ident is not None
                and failed_worker is not threading.current_thread()
            ):
                failed_worker.join(timeout=_TASK_STOP_TIMEOUT_SECONDS)
            return background_result

        return self._run_task(
            task=task,
            prompt=prompt,
        )

    def _resume_task(
        self,
        resume: str,
        subagent_type: str,
        status: TaskStatus = TaskStatus.RUNNING,
    ) -> Task:
        """Resume a sub-agent task."""
        with self._tasks_lock:
            if resume not in self._tasks:
                raise ValueError(
                    f"Task '{resume}' not found. "
                    f"Available tasks: {', '.join(sorted(self._tasks))}"
                )

            existing = self._tasks[resume]
            if not existing.settled:
                if existing.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                    raise ValueError(f"Task '{resume}' is already active.")
                raise ValueError(f"Task '{resume}' is still settling.")

            factory = get_agent_factory(subagent_type)
            worker_agent = self._get_sub_agent_from_factory(factory)
            conversation_id = self._tasks[resume].conversation_id
            with detached_delegate_context() as link:
                conversation = LocalConversation(
                    agent=worker_agent,
                    workspace=self.parent_conversation.state.workspace.working_dir,
                    persistence_dir=self._persistence_dir,
                    conversation_id=conversation_id,
                    hook_config=factory.definition.hooks,
                    delete_on_close=True,
                    observability_metadata=self._delegate_observability_metadata(
                        task_id=resume, subagent_type=subagent_type, link=link
                    ),
                    observability_tags=["delegate"],
                )

            try:
                self._set_confirmation_policy(
                    conversation,
                    factory.definition.get_confirmation_policy(),
                )

                existing.conversation = conversation
                existing.status = status
                existing.subagent = subagent_type
                existing.result = None
                existing.error = None
                existing.thread = None
                existing.stop_requested = False
                existing.metrics_settled = False
                existing.settlement_started = False
                existing.settled = False
                existing.completion_event.clear()
                existing.wait_event.clear()
                return existing
            except BaseException:
                self._close_unpublished_conversation(resume, conversation)
                raise

    def _create_task(
        self,
        subagent_type: str,
        description: str | None,
        status: TaskStatus = TaskStatus.RUNNING,
    ) -> Task:
        """Create a fresh task.

        The iteration limit is resolved with the following precedence:
        1. ``factory.definition.max_iteration_per_run`` (from the agent definition)
        2. The parent conversation's ``max_iteration_per_run``
        """
        factory = get_agent_factory(subagent_type)
        worker_agent = self._get_sub_agent_from_factory(factory)

        effective_max_iter = (
            factory.definition.max_iteration_per_run
            if factory.definition.max_iteration_per_run
            else self.parent_conversation.max_iteration_per_run
        )
        # Sub-agent budget: definition value, else inherit the parent's.
        effective_max_budget = (
            factory.definition.max_budget_per_run
            or self.parent_conversation.max_budget_per_run
        )

        with self._tasks_lock:
            task_id, conversation_id = self._generate_ids()

            sub_conversation = self._get_conversation(
                description=description,
                max_iteration_per_run=effective_max_iter,
                max_budget_per_run=effective_max_budget,
                task_id=task_id,
                subagent_type=subagent_type,
                worker_agent=worker_agent,
                conversation_id=conversation_id,
                hook_config=factory.definition.hooks,
            )

            try:
                self._set_confirmation_policy(
                    sub_conversation,
                    factory.definition.get_confirmation_policy(),
                )

                task = Task(
                    id=task_id,
                    conversation_id=conversation_id,
                    conversation=sub_conversation,
                    status=status,
                    subagent=subagent_type,
                )
                self._reserve_parent_metrics(task_id)
                self._tasks[task_id] = task
                return task
            except BaseException:
                self._discard_parent_metrics(task_id)
                self._close_unpublished_conversation(task_id, sub_conversation)
                raise

    def _reserve_parent_metrics(self, task_id: str) -> None:
        """Reserve a stable parent metrics slot before a worker can finish."""
        parent = self.parent_conversation
        runtime = self._parent_runtime
        if runtime is None:
            parent.conversation_stats.usage_to_metrics.setdefault(
                f"task:{task_id}", Metrics()
            )
            return
        with runtime.lock:
            parent.conversation_stats.usage_to_metrics.setdefault(
                f"task:{task_id}", Metrics()
            )

    def _discard_parent_metrics(self, task_id: str) -> None:
        """Remove a metrics slot for a task that was never published."""
        parent = self._parent_conversation
        if parent is None:
            return
        try:
            runtime = self._parent_runtime
            if runtime is None:
                parent.conversation_stats.usage_to_metrics.pop(f"task:{task_id}", None)
                return
            with runtime.lock:
                parent.conversation_stats.usage_to_metrics.pop(f"task:{task_id}", None)
        except BaseException as e:
            logger.warning(
                "Failed to discard metrics for unpublished task '%s': %s",
                task_id,
                e,
            )

    def _get_conversation(
        self,
        description: str | None,
        max_iteration_per_run: int,
        task_id: str,
        subagent_type: str,
        conversation_id: uuid.UUID,
        worker_agent: Agent,
        hook_config: HookConfig | None = None,
        max_budget_per_run: float | None = None,
    ) -> LocalConversation:
        parent = self.parent_conversation
        parent_visualizer = parent._visualizer

        visualizer = None
        if parent_visualizer is not None:
            label = description or task_id
            visualizer = parent_visualizer.create_sub_visualizer(label)

        with detached_delegate_context() as link:
            return LocalConversation(
                agent=worker_agent,
                workspace=parent.state.workspace.working_dir,
                visualizer=visualizer,
                persistence_dir=self._persistence_dir,
                conversation_id=conversation_id,
                max_iteration_per_run=max_iteration_per_run,
                max_budget_per_run=max_budget_per_run,
                hook_config=hook_config,
                delete_on_close=True,
                prompt_cache_key=str(parent.state.id),
                observability_metadata=self._delegate_observability_metadata(
                    task_id=task_id, subagent_type=subagent_type, link=link
                ),
                observability_tags=["delegate"],
            )

    def _delegate_observability_metadata(
        self,
        task_id: str,
        subagent_type: str,
        link: dict[str, TraceMetadataValue],
    ) -> dict[str, TraceMetadataValue]:
        """Trace metadata identifying a delegate conversation to its task.

        ``link`` is the parent-trace linkage yielded by
        ``detached_delegate_context`` (``delegate.parent_trace_id``/
        ``delegate.parent_span_id``/``tool_call_id``, best-effort).
        """
        return {
            "is_delegate": True,
            "task_id": task_id,
            "subagent_type": subagent_type,
            "parent_session_id": str(self.parent_conversation.state.id),
            **link,
        }

    def _get_sub_agent(self, subagent_type: str) -> Agent:
        """Return the subagent assigned to the task.

        Raises:
            ValueError: If the subagent type is invalid.
        """
        factory = get_agent_factory(subagent_type)
        return self._get_sub_agent_from_factory(factory)

    def _get_sub_agent_from_factory(self, factory: "AgentFactory") -> Agent:
        """Create a sub-agent from an AgentFactory."""
        parent = self.parent_conversation
        parent_llm = parent.agent.llm

        llm_updates: dict = {"stream": False}
        sub_agent_llm = parent_llm.model_copy(update=llm_updates)
        # Reset metrics such that the sub-agent has its own
        # Metrics object
        sub_agent_llm.reset_metrics()

        sub_agent = factory.factory_func(sub_agent_llm)

        # ensuring that the sub-agent LLM has stream deactivated
        sub_agent = sub_agent.model_copy(
            update={"llm": sub_agent.llm.model_copy(update={"stream": False})}
        )
        return sub_agent

    def _run_task(self, task: Task, prompt: str) -> Task:
        """Run a task synchronously."""
        if task.conversation is None:
            raise RuntimeError(f"Task '{task.id}' has no conversation to run.")
        # Get parent name for sender info
        parent_name = None
        parent = self.parent_conversation
        if hasattr(parent, "_visualizer") and parent._visualizer is not None:
            parent_name = getattr(parent._visualizer, "_name", None)

        try:
            with self._tasks_lock:
                if task.stop_requested:
                    task.set_cancelled()
                    return task
            task.conversation.send_message(prompt, sender=parent_name)
            self._run_until_finished(task.id, task.conversation)
            status = task.conversation.state.execution_status
            with self._tasks_lock:
                if task.stop_requested:
                    result, _ = self._extract_final_response(task.conversation)
                    task.set_cancelled(result)
                elif status == ConversationExecutionStatus.FINISHED:
                    result = get_agent_final_response(task.conversation.state.events)
                    task.set_result(result)
                    logger.info(f"Task '{task.id}' completed.")
                else:
                    # Any non-FINISHED terminal status (run-limit, stuck, paused, ...)
                    # is surfaced as an error, not an empty "completed"; the detail
                    # keeps partial output so the parent can use/retry it.
                    task.set_error(self._run_stop_detail(task.conversation, status))
                    logger.warning(
                        f"Task '{task.id}' stopped: status '{status.value}'."
                    )
        except Exception as e:
            with self._tasks_lock:
                if task.stop_requested:
                    task.set_cancelled()
                else:
                    task.set_error(str(e) or type(e).__name__)
            logger.warning(f"Task {task.id} failed with error: {e}")
        finally:
            self._settle_task(task)

        return task

    def _start_background_task(self, task: Task, prompt: str) -> Task:
        """Publish and start one background worker without joining it."""
        start_gate = threading.Event()
        thread: threading.Thread | None = None
        try:
            with self._tasks_lock:
                if self._closed:
                    raise RuntimeError("Task manager closed before worker start.")
                thread = threading.Thread(
                    target=self._run_background_task,
                    args=(task.id, prompt, start_gate),
                    name=f"Task-{task.id}",
                    daemon=True,
                )
                task.thread = thread
                thread.start()
                start_gate.set()
        except BaseException as e:
            with self._tasks_lock:
                task.set_error(f"Failed to start background worker: {e}")
            self._settle_task(task)
            start_gate.set()
        with self._tasks_lock:
            return self._snapshot_task(task)

    def _run_background_task(
        self,
        task_id: str,
        prompt: str,
        start_gate: threading.Event,
    ) -> None:
        start_gate.wait()
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task.settled:
                return
            if task.stop_requested or self._closed:
                task.set_cancelled()
                should_run = False
            else:
                task.status = TaskStatus.RUNNING
                should_run = True

        if not should_run:
            self._settle_task(task)
            return

        conversation = task.conversation
        if conversation is None:
            with self._tasks_lock:
                task.set_error(f"Task '{task.id}' has no conversation to run.")
            self._settle_task(task)
            return

        parent = self.parent_conversation
        parent_name = None
        if parent._visualizer is not None:
            parent_name = getattr(parent._visualizer, "_name", None)

        try:
            conversation.send_message(prompt, sender=parent_name)
            asyncio.run(self._arun_until_finished(task.id, conversation))
            result, extraction_error = self._extract_final_response(conversation)
            with self._tasks_lock:
                if task.stop_requested:
                    task.set_cancelled(result)
                elif extraction_error is not None:
                    task.set_error(extraction_error)
                elif (
                    conversation.state.execution_status
                    == ConversationExecutionStatus.FINISHED
                ):
                    task.set_result(result)
                else:
                    task.set_error(
                        self._run_stop_detail_with_partial(
                            conversation,
                            conversation.state.execution_status,
                            result or "",
                        )
                    )
        except asyncio.CancelledError:
            result, extraction_error = self._extract_final_response(conversation)
            with self._tasks_lock:
                if task.stop_requested:
                    task.set_cancelled(result)
                else:
                    detail = "Task execution was cancelled unexpectedly."
                    if extraction_error is not None:
                        detail = f"{detail}\n{extraction_error}"
                    task.set_error(detail)
        except BaseException as e:
            result, extraction_error = self._extract_final_response(conversation)
            with self._tasks_lock:
                if task.stop_requested:
                    task.set_cancelled(result)
                else:
                    detail = str(e) or type(e).__name__
                    if extraction_error is not None:
                        detail = f"{detail}\n{extraction_error}"
                    task.set_error(detail)
            logger.warning("Background task %s failed: %s", task.id, e, exc_info=True)
        finally:
            self._settle_task(task)

    async def _arun_until_finished(
        self,
        task_id: str,
        conversation: LocalConversation,
    ) -> None:
        await self._arun_once(task_id, conversation)
        while (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            with self._tasks_lock:
                task = self._tasks.get(task_id)
                if task is None or task.stop_requested:
                    conversation.interrupt()
                    return

            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                return
            approved = self._confirmation_handler is None or self._confirmation_handler(
                task_id, pending
            )
            with self._tasks_lock:
                task = self._tasks.get(task_id)
                stop_requested = task is None or task.stop_requested
            if stop_requested:
                conversation.interrupt()
                return
            if approved:
                await self._arun_once(task_id, conversation)
            else:
                conversation.reject_pending_actions("User rejected the actions")
                with self._tasks_lock:
                    task = self._tasks.get(task_id)
                    stop_requested = task is None or task.stop_requested
                if stop_requested:
                    conversation.interrupt()
                    return
                await self._arun_once(task_id, conversation)

    async def _arun_once(
        self,
        task_id: str,
        conversation: LocalConversation,
    ) -> None:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task.stop_requested:
                conversation.interrupt()
                return
        run_task = asyncio.create_task(conversation.arun())
        await asyncio.sleep(0)
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            stop_requested = task is None or task.stop_requested
        if stop_requested:
            conversation.interrupt()
        await run_task

    @staticmethod
    def _extract_final_response(
        conversation: LocalConversation,
    ) -> tuple[str | None, str | None]:
        try:
            result = get_agent_final_response(conversation.state.events)
        except BaseException as e:
            return None, f"Failed to extract task response: {e or type(e).__name__}"
        return result or None, None

    def _settle_task(self, task: Task) -> None:
        """Settle metrics, conversation resources, and completion exactly once."""
        with self._tasks_lock:
            if task.settlement_started:
                return
            if task.status not in _TERMINAL_TASK_STATUSES:
                if task.stop_requested:
                    task.set_cancelled(task.result)
                else:
                    task.set_error("Task ended without reaching a terminal state.")
            task.settlement_started = True
            should_settle_metrics = not task.metrics_settled
            task.metrics_settled = True

        if should_settle_metrics:
            try:
                self._update_parent_metrics(self.parent_conversation, task)
            except BaseException as e:
                logger.warning("Failed to settle metrics for task '%s': %s", task.id, e)

        try:
            self._evict_task(task)
        finally:
            with self._tasks_lock:
                task.settled = True
                stored = self._tasks.get(task.id)
                if stored is not None:
                    stored.settled = True
                task.completion_event.set()
                task.wait_event.set()
            self._finish_cleanup_if_idle()

    def get_task(
        self,
        task_id: str,
        block: bool = False,
        timeout: float = 30.0,
    ) -> Task:
        """Return a stable task snapshot, optionally waiting for completion."""
        if not math.isfinite(timeout) or not 0 <= timeout <= 3600:
            raise ValueError("timeout must be between 0 and 3600 seconds.")
        with self._tasks_lock:
            task = self._require_task(task_id)
            wait_event = task.wait_event
            terminal = task.status in _TERMINAL_TASK_STATUSES
        if block and not terminal:
            wait_event.wait(timeout=timeout)
        with self._tasks_lock:
            current = self._tasks.get(task_id, task)
            return self._snapshot_task(current)

    def stop_task(self, task_id: str) -> Task:
        """Request cooperative cancellation and wait for a bounded cleanup."""
        with self._tasks_lock:
            task = self._require_task(task_id)
            if task.status in {TaskStatus.COMPLETED, TaskStatus.ERROR}:
                raise ValueError(
                    f"Task '{task_id}' cannot be stopped because it is "
                    f"{task.status.value}."
                )
            should_interrupt = (
                task.status != TaskStatus.CANCELLED and not task.stop_requested
            )
            if task.status != TaskStatus.CANCELLED:
                task.stop_requested = True
                task.wait_event.set()
            conversation = task.conversation
            thread = task.thread
            completion_event = task.completion_event

        if should_interrupt and conversation is not None:
            self._interrupt_conversation(task.id, conversation)

        deadline = time.monotonic() + _TASK_STOP_TIMEOUT_SECONDS
        completion_event.wait(timeout=_TASK_STOP_TIMEOUT_SECONDS)
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.ident is not None
        ):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

        with self._tasks_lock:
            current = self._tasks.get(task_id, task)
            return self._snapshot_task(current)

    def interrupt(self) -> None:
        """Cooperatively interrupt every active task owned by this manager."""
        with self._tasks_lock:
            active = [
                task
                for task in self._tasks.values()
                if task.status not in _TERMINAL_TASK_STATUSES
            ]
            for task in active:
                task.stop_requested = True
                task.wait_event.set()

        for task in active:
            if task.conversation is not None:
                self._interrupt_conversation(task.id, task.conversation)

    def _require_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            available = ", ".join(sorted(self._tasks)) or "none"
            raise ValueError(
                f"Task '{task_id}' not found. Available tasks: {available}."
            )
        return task

    @staticmethod
    def _snapshot_task(task: Task) -> Task:
        return task.model_copy()

    @staticmethod
    def _interrupt_conversation(
        task_id: str,
        conversation: LocalConversation,
    ) -> None:
        try:
            conversation.interrupt()
        except BaseException as e:
            logger.warning("Failed to interrupt task '%s': %s", task_id, e)

    @staticmethod
    def _run_stop_detail(
        conversation: LocalConversation,
        status: ConversationExecutionStatus,
    ) -> str:
        """Why a sub-agent stopped without finishing (run-limit, stuck, paused, ...),
        plus any partial output so the parent isn't left with nothing to use."""
        partial = get_agent_final_response(conversation.state.events)
        return TaskManager._run_stop_detail_with_partial(
            conversation,
            status,
            partial,
        )

    @staticmethod
    def _run_stop_detail_with_partial(
        conversation: LocalConversation,
        status: ConversationExecutionStatus,
        partial: str | None,
    ) -> str:
        errors = [
            e
            for e in conversation.state.events
            if isinstance(e, ConversationErrorEvent)
        ]
        reason = (
            errors[-1].detail
            if errors
            else f"Sub-agent stopped without finishing (status: {status.value})."
        )
        return f"{reason}\nPartial result:\n{partial}" if partial else reason

    def _run_until_finished(
        self, task_id: str, conversation: LocalConversation
    ) -> None:
        """Run a sub-agent conversation to completion, handling confirmations."""
        conversation.run()
        while (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            with self._tasks_lock:
                task = self._tasks.get(task_id)
                if task is None or task.stop_requested:
                    conversation.interrupt()
                    return

            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                break

            approved = self._confirmation_handler is None or self._confirmation_handler(
                task_id, pending
            )
            with self._tasks_lock:
                task = self._tasks.get(task_id)
                stop_requested = task is None or task.stop_requested
            if stop_requested:
                conversation.interrupt()
                return
            if approved:
                conversation.run()
            else:
                conversation.reject_pending_actions("User rejected the actions")
                with self._tasks_lock:
                    task = self._tasks.get(task_id)
                    stop_requested = task is None or task.stop_requested
                if stop_requested:
                    conversation.interrupt()
                    return
                conversation.run()

    def _set_confirmation_policy(
        self,
        conversation: LocalConversation,
        confirmation_policy: ConfirmationPolicyBase | None,
    ) -> None:
        """
        Apply permission_mode: explicit mode from definition
        or inherit the parent's policy when None.
        """
        if confirmation_policy is None:
            conversation.set_confirmation_policy(
                self.parent_conversation.state.confirmation_policy
            )
        else:
            conversation.set_confirmation_policy(confirmation_policy)

    def _update_parent_metrics(self, parent: LocalConversation, task: Task) -> None:
        """
        Sync sub-agent metrics into parent before eviction destroys the conversation.
        Replace (not merge) because sub-agent metrics are cumulative across resumes.
        """
        if task.conversation is None:
            return
        metrics = task.conversation.conversation_stats.get_combined_metrics()
        runtime = self._parent_runtime
        if runtime is None:
            parent.conversation_stats.usage_to_metrics[f"task:{task.id}"] = metrics
            return
        with runtime.lock:
            parent.conversation_stats.usage_to_metrics[f"task:{task.id}"] = metrics

    def close(self) -> None:
        """Cooperatively stop active work and clean up within one deadline."""
        with self._tasks_lock:
            if self._cleanup_complete:
                return
            self._closed = True
            tasks = list(self._tasks.values())
            active = [
                task for task in tasks if task.status not in _TERMINAL_TASK_STATUSES
            ]
            for task in active:
                task.stop_requested = True
                task.wait_event.set()

        for task in active:
            if task.conversation is not None:
                self._interrupt_conversation(task.id, task.conversation)

        # A terminal task whose caller bypassed _settle_task, or an active task
        # with no live execution owner, can be finalized locally.
        for task in tasks:
            thread = task.thread
            has_live_owner = thread is not None and thread.is_alive()
            if not task.settlement_started and (
                task.status in _TERMINAL_TASK_STATUSES or not has_live_owner
            ):
                if task.status not in _TERMINAL_TASK_STATUSES:
                    task.set_cancelled()
                self._settle_task(task)

        deadline = time.monotonic() + _TASK_STOP_TIMEOUT_SECONDS
        for task in active:
            task.completion_event.wait(timeout=max(0.0, deadline - time.monotonic()))

        for task in tasks:
            thread = task.thread
            if (
                thread is not None
                and thread is not threading.current_thread()
                and thread.ident is not None
            ):
                thread.join(timeout=max(0.0, deadline - time.monotonic()))

        self._finish_cleanup_if_idle()

    def _finish_cleanup_if_idle(self) -> None:
        """Release manager-owned state after all task resources are settled."""
        with self._tasks_lock:
            if self._cleanup_complete or not self._closed:
                return
            if any(not task.settled for task in self._tasks.values()):
                return

            parent_persists = (
                self._parent_conversation is not None
                and self._parent_conversation.state.persistence_dir is not None
            )
            persistence_dir = self._persistence_dir
            self._tasks.clear()
            self._cleanup_complete = True

        # Persisted sub-agent conversations belong to the parent's directory.
        if (
            not parent_persists
            and persistence_dir is not None
            and persistence_dir.exists()
        ):
            shutil.rmtree(persistence_dir, ignore_errors=True)
