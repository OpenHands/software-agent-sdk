"""Implementation of delegate tool executor."""

import asyncio
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.subagent import get_agent_factory
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.delegate.definition import (
    DelegateObservation,
    DelegateTaskStatus,
)


if TYPE_CHECKING:
    from openhands.sdk.event import ActionEvent
    from openhands.tools.delegate.definition import DelegateAction

logger = get_logger(__name__)

_SUBAGENTS_DIR: Final[str] = "subagents"
_BACKGROUND_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
_TERMINAL_TASK_STATUSES: Final[frozenset[DelegateTaskStatus]] = frozenset(
    {
        DelegateTaskStatus.COMPLETED,
        DelegateTaskStatus.FAILED,
        DelegateTaskStatus.CANCELLED,
    }
)

# Called when a sub-agent hits WAITING_FOR_CONFIRMATION.
# Receives (agent_id, pending_actions) and returns True to approve, False to reject.
ConfirmationHandler = Callable[[str, list["ActionEvent"]], bool]


@dataclass(slots=True)
class _BackgroundTask:
    task_id: str
    agent_id: str
    prompt: str
    conversation: LocalConversation
    status: DelegateTaskStatus = DelegateTaskStatus.QUEUED
    result: str | None = None
    error: str | None = None
    thread: threading.Thread | None = None
    stop_requested: bool = False
    metrics_settled: bool = False


class DelegateExecutor(ToolExecutor):
    """Executor for delegation operations.

    This class handles:
    - Spawning sub-agents with meaningful string identifiers (e.g., 'refactor_module')
    - Delegating tasks to sub-agents and waiting for results (blocking)
    - Running explicitly requested background tasks with an in-process lifecycle
    """

    def __init__(
        self,
        max_children: int = 5,
        confirmation_handler: ConfirmationHandler | None = None,
    ):
        self._parent_conversation: LocalConversation | None = None
        # Map from user-friendly identifier to conversation
        self._sub_agents: dict[str, LocalConversation] = {}
        self._max_children: int = max_children
        self._confirmation_handler = confirmation_handler
        self._background_tasks: dict[str, _BackgroundTask] = {}
        self._active_agents: dict[str, str] = {}
        self._lock = threading.RLock()
        self._spawn_lock = threading.Lock()
        self._closed = False

    @property
    def parent_conversation(self) -> LocalConversation:
        """Get the parent conversation.

        Raises:
            RuntimeError: If parent conversation has not been set yet.
        """
        if self._parent_conversation is None:
            raise RuntimeError(
                "Parent conversation not set. This should be set automatically "
                "on the first call to the executor."
            )
        return self._parent_conversation

    def __call__(
        self,
        action: "DelegateAction",
        conversation: LocalConversation | None = None,
    ) -> DelegateObservation:
        """Execute a delegation or background-task lifecycle action."""
        if conversation is None:
            return DelegateObservation.from_text(
                text="A parent conversation is required for delegation",
                command=action.command,
                is_error=True,
            )

        with self._lock:
            if self._closed:
                return DelegateObservation.from_text(
                    text="Delegate executor is closed",
                    command=action.command,
                    is_error=True,
                )
            if self._parent_conversation is None:
                self._parent_conversation = conversation
            elif self._parent_conversation is not conversation:
                return DelegateObservation.from_text(
                    text=(
                        "Delegate executor is bound to a different parent conversation"
                    ),
                    command=action.command,
                    is_error=True,
                )

        if action.command == "spawn":
            with self._spawn_lock:
                return self._spawn_agents(action)
        elif action.command == "delegate":
            return self._delegate_tasks(action)
        elif action.command == "status":
            return self._background_status(action)
        elif action.command == "output":
            return self._background_output(action)
        elif action.command == "stop":
            return self._stop_background_task(action)
        else:
            return DelegateObservation.from_text(
                text=(
                    f"Unsupported command: {action.command}. "
                    "Available commands: spawn, delegate, status, output, stop"
                ),
                command=action.command,
                is_error=True,
            )

    @staticmethod
    def _format_agent_label(agent_id: str, agent_type: str) -> str:
        """Compose a friendly label for logging and user messages."""
        type_suffix = " (default)" if agent_type == "default" else f" ({agent_type})"
        return f"{agent_id}{type_suffix}"

    def _resolve_agent_type(self, action: "DelegateAction", index: int) -> str:
        """Get the agent type for a given index, defaulting to the general agent."""
        if not action.agent_types or index >= len(action.agent_types):
            return "default"
        return action.agent_types[index].strip() or "default"

    def _close_sub_agent(self, agent_id: str, conversation: LocalConversation) -> None:
        try:
            conversation.close()
        except Exception as e:
            logger.warning(f"Error closing sub-agent '{agent_id}': {e}")

    def interrupt(self) -> None:
        """Cooperatively cancel all active delegate conversations."""
        with self._lock:
            background_tasks = [
                task
                for task in self._background_tasks.values()
                if task.status not in _TERMINAL_TASK_STATUSES
            ]
            for task in background_tasks:
                task.stop_requested = True

            blocking_conversations = [
                self._sub_agents[agent_id]
                for agent_id, operation_id in self._active_agents.items()
                if operation_id.startswith("blocking:") and agent_id in self._sub_agents
            ]

        for task in background_tasks:
            self._interrupt_sub_agent(task.agent_id, task.conversation)
        for conversation in blocking_conversations:
            self._interrupt_sub_agent("blocking", conversation)

    def close(self) -> None:
        """Cancel workers, wait for bounded cleanup, then close sub-agents."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            background_tasks = list(self._background_tasks.values())
            active_background_tasks = [
                task
                for task in background_tasks
                if task.status not in _TERMINAL_TASK_STATUSES
            ]
            for task in active_background_tasks:
                task.stop_requested = True
            sub_agents = list(self._sub_agents.items())
            blocking_conversations = [
                self._sub_agents[agent_id]
                for agent_id, operation_id in self._active_agents.items()
                if operation_id.startswith("blocking:") and agent_id in self._sub_agents
            ]

        for task in active_background_tasks:
            self._interrupt_sub_agent(task.agent_id, task.conversation)
        for conversation in blocking_conversations:
            self._interrupt_sub_agent("blocking", conversation)

        deadline = time.monotonic() + _BACKGROUND_JOIN_TIMEOUT_SECONDS
        for task in background_tasks:
            thread = task.thread
            if thread is None or thread is threading.current_thread():
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if thread.ident is not None:
                thread.join(timeout=remaining)
            if thread.is_alive():
                logger.warning(
                    "Background delegate task '%s' did not stop before cleanup "
                    "deadline",
                    task.task_id,
                )

        for agent_id, conversation in sub_agents:
            self._close_sub_agent(agent_id, conversation)

        with self._lock:
            self._background_tasks.clear()
            self._active_agents.clear()
            self._sub_agents.clear()

    def _interrupt_sub_agent(
        self, agent_id: str, conversation: LocalConversation
    ) -> None:
        try:
            conversation.interrupt()
        except Exception as e:
            logger.warning(f"Error interrupting sub-agent '{agent_id}': {e}")

    def _run_until_finished(
        self, agent_id: str, conversation: LocalConversation
    ) -> None:
        """Run a sub-agent conversation to completion, handling confirmations."""
        conversation.run()
        while (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                break

            if self._confirmation_handler is None or self._confirmation_handler(
                agent_id, pending
            ):
                conversation.run()
            else:
                conversation.reject_pending_actions("User rejected the actions")
                conversation.run()

    def _spawn_agents(self, action: "DelegateAction") -> DelegateObservation:
        """Spawn sub-agents with optional agent types."""
        if not action.ids:
            return DelegateObservation.from_text(
                text="At least one ID is required for spawn action",
                command=action.command,
                is_error=True,
            )

        # Validate agent_types if provided
        if action.agent_types is not None:
            if len(action.agent_types) > len(action.ids):
                return DelegateObservation.from_text(
                    text=(
                        f"agent_types length ({len(action.agent_types)}) "
                        f"cannot exceed ids length ({len(action.ids)})"
                    ),
                    command=action.command,
                    is_error=True,
                )

        operation_id = f"spawn:{uuid.uuid4().hex}"
        with self._lock:
            busy_agents = {
                agent_id: self._active_agents[agent_id]
                for agent_id in action.ids
                if agent_id in self._active_agents
            }
            if busy_agents:
                return self._busy_agents_observation(action, busy_agents)

            new_agent_ids = set(action.ids) - set(self._sub_agents)
            if len(self._sub_agents) + len(new_agent_ids) > self._max_children:
                return DelegateObservation.from_text(
                    text=(
                        f"Cannot spawn {len(action.ids)} agents. "
                        f"Already have {len(self._sub_agents)} agents, "
                        f"maximum is {self._max_children}"
                    ),
                    command=action.command,
                    is_error=True,
                )
            for agent_id in action.ids:
                self._active_agents[agent_id] = operation_id

        created_sub_agents: list[tuple[str, str, LocalConversation]] = []
        try:
            parent_conversation = self.parent_conversation
            parent_llm = parent_conversation.agent.llm
            parent_visualizer = parent_conversation._visualizer
            workspace_path = parent_conversation.state.workspace.working_dir

            resolved_agent_types = [
                self._resolve_agent_type(action, i) for i in range(len(action.ids))
            ]
            factories = [
                get_agent_factory(name=agent_type)
                for agent_type in resolved_agent_types
            ]

            for agent_id, agent_type, factory in zip(
                action.ids, resolved_agent_types, factories
            ):
                sub_agent_llm = parent_llm.model_copy()
                # resetting metrics such that the sub-agent has its own
                # Metrics object
                sub_agent_llm.reset_metrics()

                worker_agent = factory.factory_func(sub_agent_llm)

                # ensuring that the sub-agent LLM has stream deactivated
                worker_agent = worker_agent.model_copy(
                    update={
                        "llm": worker_agent.llm.model_copy(update={"stream": False})
                    }
                )

                # Use parent visualizer's create_sub_visualizer method if available
                # This allows custom visualizers (e.g., TUI-based) to create
                # appropriate sub-visualizers for their environment
                sub_visualizer = None
                if parent_visualizer is not None:
                    sub_visualizer = parent_visualizer.create_sub_visualizer(agent_id)

                # Inherit persistence from the parent conversation:
                # if the parent persists its conversation, subagents persist
                # theirs under a "subagents" subdirectory.
                parent_persistence_dir = parent_conversation.state.persistence_dir
                if parent_persistence_dir is not None:
                    subagents_persistence_dir: Path | None = (
                        Path(parent_persistence_dir) / _SUBAGENTS_DIR
                    )
                    subagents_persistence_dir.mkdir(parents=True, exist_ok=True)
                else:
                    subagents_persistence_dir = None

                # Use max_iteration_per_run from agent definition if set
                conv_kwargs: dict = {
                    "agent": worker_agent,
                    "workspace": workspace_path,
                    "visualizer": sub_visualizer,
                    "hook_config": factory.definition.hooks,
                    "persistence_dir": subagents_persistence_dir,
                }

                if factory.definition.max_iteration_per_run is not None:
                    conv_kwargs["max_iteration_per_run"] = (
                        factory.definition.max_iteration_per_run
                    )

                sub_conversation = LocalConversation(**conv_kwargs)
                created_sub_agents.append((agent_id, agent_type, sub_conversation))

                # Apply permission_mode: explicit mode from definition,
                # or inherit the parent's policy when None.
                confirmation_policy = factory.definition.get_confirmation_policy()
                if confirmation_policy is None:
                    sub_conversation.set_confirmation_policy(
                        parent_conversation.state.confirmation_policy
                    )
                else:
                    sub_conversation.set_confirmation_policy(confirmation_policy)

            replaced_sub_agents: list[tuple[str, LocalConversation]] = []
            with self._lock:
                if self._closed:
                    raise RuntimeError("Delegate executor closed during spawn")
                for agent_id, _, sub_conversation in created_sub_agents:
                    previous_conversation = self._sub_agents.get(agent_id)
                    if previous_conversation is not None:
                        replaced_sub_agents.append((agent_id, previous_conversation))
                    self._sub_agents[agent_id] = sub_conversation

            for agent_id, previous_conversation in replaced_sub_agents:
                self._close_sub_agent(agent_id, previous_conversation)

            for agent_id, agent_type, _ in created_sub_agents:
                # Log what type of agent was created
                logger.info(
                    f"Spawned sub-agent '{self._format_agent_label(agent_id, agent_type)}'"  # noqa: E501
                )

            # Create success message with details
            agent_details = [
                self._format_agent_label(agent_id, agent_type)
                for agent_id, agent_type in zip(action.ids, resolved_agent_types)
            ]

            message = (
                f"Successfully spawned {len(action.ids)} sub-agents: "
                f"{', '.join(agent_details)}"
            )
            self._release_agents(action.ids, operation_id)
            return DelegateObservation.from_text(
                text=message,
                command=action.command,
            )

        except Exception as e:
            for agent_id, _, sub_conversation in created_sub_agents:
                self._close_sub_agent(agent_id, sub_conversation)
            self._release_agents(action.ids, operation_id)
            logger.error(f"Error: failed to spawn agents: {e}", exc_info=True)
            return DelegateObservation.from_text(
                text=f"failed to spawn agents: {str(e)}",
                command=action.command,
                is_error=True,
            )

    def _release_agents(self, agent_ids: list[str], operation_id: str) -> None:
        with self._lock:
            for agent_id in agent_ids:
                if self._active_agents.get(agent_id) == operation_id:
                    del self._active_agents[agent_id]

    def _busy_agents_observation(
        self,
        action: "DelegateAction",
        busy_agents: dict[str, str],
    ) -> DelegateObservation:
        details = ", ".join(
            f"{agent_id} ({operation_id})"
            for agent_id, operation_id in sorted(busy_agents.items())
        )
        return DelegateObservation.from_text(
            text=f"sub-agents already have active tasks: {details}",
            command=action.command,
            is_error=True,
        )

    def _reserve_existing_agents(
        self,
        action: "DelegateAction",
        reservations: dict[str, str],
    ) -> tuple[dict[str, LocalConversation], DelegateObservation | None]:
        with self._lock:
            if self._closed:
                return {}, DelegateObservation.from_text(
                    text="Delegate executor is closed",
                    command=action.command,
                    is_error=True,
                )
            missing_agents = set(reservations) - set(self._sub_agents)
            if missing_agents:
                available_agents = ", ".join(sorted(self._sub_agents))
                return {}, DelegateObservation.from_text(
                    text=(
                        f"sub-agents not found: {', '.join(sorted(missing_agents))}. "
                        f"Available agents: {available_agents}"
                    ),
                    command=action.command,
                    is_error=True,
                )

            busy_agents = {
                agent_id: self._active_agents[agent_id]
                for agent_id in reservations
                if agent_id in self._active_agents
            }
            if busy_agents:
                return {}, self._busy_agents_observation(action, busy_agents)

            conversations = {
                agent_id: self._sub_agents[agent_id] for agent_id in reservations
            }
            self._active_agents.update(reservations)
            return conversations, None

    def _delegate_tasks(self, action: "DelegateAction") -> DelegateObservation:
        """Delegate tasks synchronously unless background execution is requested."""
        if not action.tasks:
            return DelegateObservation.from_text(
                text="at least one task is required for delegate action",
                command=action.command,
                is_error=True,
            )
        if action.background:
            return self._start_background_tasks(action)
        return self._delegate_tasks_blocking(action)

    def _delegate_tasks_blocking(self, action: "DelegateAction") -> DelegateObservation:
        tasks = action.tasks
        if tasks is None:
            raise RuntimeError("delegate tasks were not validated")

        operation_id = f"blocking:{uuid.uuid4().hex}"
        reservations = {agent_id: operation_id for agent_id in tasks}
        conversations, error = self._reserve_existing_agents(action, reservations)
        if error is not None:
            return error

        results: dict[str, str] = {}
        errors: dict[str, str] = {}
        results_lock = threading.Lock()
        parent_conversation = self.parent_conversation
        visualizer = parent_conversation._visualizer
        parent_name = getattr(visualizer, "_name", None) if visualizer else None

        def run_task(
            agent_id: str,
            conversation: LocalConversation,
            prompt: str,
        ) -> None:
            try:
                logger.info(f"Sub-agent {agent_id} starting task: {prompt[:100]}...")
                conversation.send_message(prompt, sender=parent_name)
                self._run_until_finished(agent_id, conversation)
                final_response = get_agent_final_response(conversation.state.events)
                result = final_response or "No response from sub-agent"
                with results_lock:
                    results[agent_id] = result
                if final_response:
                    logger.info(f"Sub-agent {agent_id} completed successfully")
                else:
                    logger.warning(
                        f"Sub-agent {agent_id} completed but no final response"
                    )
            except Exception as e:
                error_message = f"Sub-agent {agent_id} failed: {str(e)}"
                with results_lock:
                    errors[agent_id] = error_message
                logger.error(error_message, exc_info=True)

        try:
            threads: list[threading.Thread] = []
            for agent_id, prompt in tasks.items():
                thread = threading.Thread(
                    target=run_task,
                    args=(agent_id, conversations[agent_id], prompt),
                    name=f"Task-{agent_id}",
                )
                thread.start()
                threads.append(thread)

            for thread in threads:
                thread.join()

            all_results: list[str] = []
            for agent_id in tasks:
                if agent_id in results:
                    all_results.append(f"Agent {agent_id}: {results[agent_id]}")
                elif agent_id in errors:
                    all_results.append(f"Agent {agent_id} ERROR: {errors[agent_id]}")
                else:
                    all_results.append(f"Agent {agent_id}: No result")

            output_text = f"Completed delegation of {len(tasks)} tasks"
            if errors:
                output_text += f" with {len(errors)} errors"
            if all_results:
                results_text = "\n".join(
                    f"{index}. {result}" for index, result in enumerate(all_results, 1)
                )
                output_text += f"\n\nResults:\n{results_text}"

            return DelegateObservation.from_text(
                text=output_text,
                command=action.command,
            )
        except Exception as e:
            logger.error(f"Failed to delegate tasks: {e}", exc_info=True)
            return DelegateObservation.from_text(
                text=f"failed to delegate tasks: {str(e)}",
                command=action.command,
                is_error=True,
            )
        finally:
            for agent_id, conversation in conversations.items():
                self._sync_agent_metrics(agent_id, conversation)
            self._release_agents(list(tasks), operation_id)

    def _start_background_tasks(self, action: "DelegateAction") -> DelegateObservation:
        tasks = action.tasks
        if tasks is None:
            raise RuntimeError("delegate tasks were not validated")

        with self._lock:
            task_ids = {agent_id: self._new_background_task_id() for agent_id in tasks}
        conversations, error = self._reserve_existing_agents(action, task_ids)
        if error is not None:
            return error

        failed_to_start: list[str] = []
        with self._lock:
            if self._closed:
                for agent_id, task_id in task_ids.items():
                    if self._active_agents.get(agent_id) == task_id:
                        del self._active_agents[agent_id]
                return DelegateObservation.from_text(
                    text="Delegate executor closed before background tasks started",
                    command=action.command,
                    is_error=True,
                )
            for agent_id, prompt in tasks.items():
                task_id = task_ids[agent_id]
                task = _BackgroundTask(
                    task_id=task_id,
                    agent_id=agent_id,
                    prompt=prompt,
                    conversation=conversations[agent_id],
                )
                self._background_tasks[task_id] = task
                try:
                    thread = threading.Thread(
                        target=self._run_background_task,
                        args=(task_id,),
                        name=f"Delegate-{agent_id}-{task_id[-8:]}",
                        daemon=True,
                    )
                    task.thread = thread
                    thread.start()
                except Exception as e:
                    task.status = DelegateTaskStatus.FAILED
                    task.error = f"Failed to start background worker: {str(e)}"
                    task.metrics_settled = True
                    failed_to_start.append(agent_id)
                    if self._active_agents.get(agent_id) == task_id:
                        del self._active_agents[agent_id]

        lines = [f"- {agent_id}: {task_id}" for agent_id, task_id in task_ids.items()]
        output = (
            f"Started {len(tasks) - len(failed_to_start)} background delegation "
            f"tasks\n" + "\n".join(lines)
        )
        if failed_to_start:
            output += f"\nFailed to start: {', '.join(failed_to_start)}"
        return DelegateObservation.from_text(
            text=output,
            command=action.command,
            task_ids=task_ids,
            is_error=bool(failed_to_start),
        )

    def _new_background_task_id(self) -> str:
        while True:
            task_id = f"delegate_{uuid.uuid4().hex}"
            if task_id not in self._background_tasks:
                return task_id

    def _run_background_task(self, task_id: str) -> None:
        with self._lock:
            task = self._background_tasks.get(task_id)
            if task is None:
                return
            if task.stop_requested:
                should_run = False
            else:
                task.status = DelegateTaskStatus.RUNNING
                should_run = True

        if not should_run:
            self._settle_background_task(
                task_id,
                status=DelegateTaskStatus.CANCELLED,
            )
            return

        logger.info(
            "Sub-agent %s starting background task %s: %s...",
            task.agent_id,
            task_id,
            task.prompt[:100],
        )
        try:
            parent_visualizer = self.parent_conversation._visualizer
            parent_name = (
                getattr(parent_visualizer, "_name", None) if parent_visualizer else None
            )
            task.conversation.send_message(task.prompt, sender=parent_name)
            asyncio.run(
                self._arun_until_finished(
                    task_id,
                    task.agent_id,
                    task.conversation,
                )
            )

            final_response, extraction_error = self._extract_background_response(
                task.conversation
            )
            with self._lock:
                stop_requested = task.stop_requested
            if stop_requested:
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.CANCELLED,
                    result=final_response,
                )
            elif extraction_error is not None:
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.FAILED,
                    error=extraction_error,
                )
            elif (
                task.conversation.state.execution_status
                == ConversationExecutionStatus.FINISHED
            ):
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.COMPLETED,
                    result=final_response or "No response from sub-agent",
                )
            else:
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.FAILED,
                    error=self._background_failure_detail(
                        task.conversation,
                        final_response or "",
                    ),
                )
        except asyncio.CancelledError:
            with self._lock:
                stop_requested = task.stop_requested
            result, extraction_error = self._extract_background_response(
                task.conversation
            )
            if stop_requested:
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.CANCELLED,
                    result=result,
                )
            else:
                error = "Sub-agent execution was cancelled unexpectedly"
                if extraction_error is not None:
                    error = f"{error}\n{extraction_error}"
                logger.error(
                    "Sub-agent %s failed background task %s: %s",
                    task.agent_id,
                    task_id,
                    error,
                )
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.FAILED,
                    error=error,
                )
        except Exception as e:
            with self._lock:
                stop_requested = task.stop_requested
            result, extraction_error = self._extract_background_response(
                task.conversation
            )
            if stop_requested:
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.CANCELLED,
                    result=result,
                )
            else:
                error = str(e)
                if extraction_error is not None:
                    error = f"{error}\n{extraction_error}"
                logger.error(
                    "Sub-agent %s failed background task %s: %s",
                    task.agent_id,
                    task_id,
                    error,
                    exc_info=True,
                )
                self._settle_background_task(
                    task_id,
                    status=DelegateTaskStatus.FAILED,
                    error=error,
                )

    async def _arun_until_finished(
        self,
        task_id: str,
        agent_id: str,
        conversation: LocalConversation,
    ) -> None:
        await self._arun_once(task_id, conversation)
        while (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            with self._lock:
                task = self._background_tasks.get(task_id)
                if task is None or task.stop_requested:
                    conversation.interrupt()
                    return

            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                return
            if self._confirmation_handler is None or self._confirmation_handler(
                agent_id, pending
            ):
                await self._arun_once(task_id, conversation)
            else:
                conversation.reject_pending_actions("User rejected the actions")
                await self._arun_once(task_id, conversation)

    async def _arun_once(
        self,
        task_id: str,
        conversation: LocalConversation,
    ) -> None:
        run_task = asyncio.create_task(conversation.arun())
        # Let arun register its cancellable task before rechecking a stop that
        # may have arrived between send_message() and event-loop startup.
        await asyncio.sleep(0)
        with self._lock:
            task = self._background_tasks.get(task_id)
            stop_requested = task is None or task.stop_requested
        if stop_requested:
            conversation.interrupt()
        await run_task

    @staticmethod
    def _extract_background_response(
        conversation: LocalConversation,
    ) -> tuple[str | None, str | None]:
        try:
            response = get_agent_final_response(conversation.state.events)
        except Exception as e:
            return None, f"Failed to extract sub-agent response: {e}"
        return response or None, None

    @staticmethod
    def _background_failure_detail(
        conversation: LocalConversation,
        partial: str,
    ) -> str:
        status = conversation.state.execution_status
        errors = [
            event
            for event in conversation.state.events
            if isinstance(event, ConversationErrorEvent)
        ]
        reason = (
            errors[-1].detail
            if errors
            else f"Sub-agent stopped without finishing (status: {status.value})."
        )
        return f"{reason}\nPartial result:\n{partial}" if partial else reason

    def _settle_background_task(
        self,
        task_id: str,
        status: DelegateTaskStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            task = self._background_tasks.get(task_id)
            if task is None or task.status in _TERMINAL_TASK_STATUSES:
                return
            if task.stop_requested:
                status = DelegateTaskStatus.CANCELLED
            should_settle_metrics = not task.metrics_settled
            task.metrics_settled = True

        if should_settle_metrics:
            self._sync_agent_metrics(task.agent_id, task.conversation)

        with self._lock:
            current_task = self._background_tasks.get(task_id)
            if current_task is None or current_task.status in _TERMINAL_TASK_STATUSES:
                return
            current_task.status = status
            current_task.result = result
            current_task.error = error
            if self._active_agents.get(current_task.agent_id) == task_id:
                del self._active_agents[current_task.agent_id]

    def _sync_agent_metrics(
        self,
        agent_id: str,
        conversation: LocalConversation,
    ) -> None:
        try:
            metrics = conversation.conversation_stats.get_combined_metrics()
            with self._lock:
                parent = self._parent_conversation
                if parent is not None:
                    parent.conversation_stats.usage_to_metrics[
                        f"delegate:{agent_id}"
                    ] = metrics
        except Exception as e:
            logger.warning(f"Error syncing metrics for sub-agent '{agent_id}': {e}")

    def _background_status(self, action: "DelegateAction") -> DelegateObservation:
        if action.task_id is None:
            return self._missing_task_id_observation(action)
        with self._lock:
            task = self._background_tasks.get(action.task_id)
            if task is None:
                return self._unknown_task_observation(action)
            status = task.status
            agent_id = task.agent_id
        return DelegateObservation.from_text(
            text=(
                f"Background task {action.task_id} for sub-agent {agent_id} "
                f"is {status.value}"
            ),
            command=action.command,
            task_id=action.task_id,
            agent_id=agent_id,
            status=status,
        )

    def _background_output(self, action: "DelegateAction") -> DelegateObservation:
        if action.task_id is None:
            return self._missing_task_id_observation(action)
        with self._lock:
            task = self._background_tasks.get(action.task_id)
            if task is None:
                return self._unknown_task_observation(action)
            status = task.status
            agent_id = task.agent_id
            result = task.result
            error = task.error

        if status == DelegateTaskStatus.COMPLETED:
            text = result or "Task completed with no result"
            is_error = False
        elif status == DelegateTaskStatus.FAILED:
            text = error or "Background task failed"
            is_error = True
        elif status == DelegateTaskStatus.CANCELLED:
            text = f"Background task {action.task_id} was cancelled"
            if result:
                text += f"\nPartial output:\n{result}"
            is_error = True
        else:
            text = (
                f"Background task {action.task_id} is not finished "
                f"(status: {status.value})"
            )
            is_error = True

        return DelegateObservation.from_text(
            text=text,
            command=action.command,
            task_id=action.task_id,
            agent_id=agent_id,
            status=status,
            is_error=is_error,
        )

    def _stop_background_task(self, action: "DelegateAction") -> DelegateObservation:
        if action.task_id is None:
            return self._missing_task_id_observation(action)
        with self._lock:
            task = self._background_tasks.get(action.task_id)
            if task is None:
                return self._unknown_task_observation(action)
            if task.status == DelegateTaskStatus.CANCELLED:
                return DelegateObservation.from_text(
                    text=f"Background task {action.task_id} is already cancelled",
                    command=action.command,
                    task_id=action.task_id,
                    agent_id=task.agent_id,
                    status=task.status,
                )
            if task.status in {
                DelegateTaskStatus.COMPLETED,
                DelegateTaskStatus.FAILED,
            }:
                return DelegateObservation.from_text(
                    text=(
                        f"Background task {action.task_id} cannot be stopped "
                        f"because it is {task.status.value}"
                    ),
                    command=action.command,
                    task_id=action.task_id,
                    agent_id=task.agent_id,
                    status=task.status,
                    is_error=True,
                )
            task.stop_requested = True
            conversation = task.conversation
            thread = task.thread
            agent_id = task.agent_id

        self._interrupt_sub_agent(agent_id, conversation)
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.ident is not None
        ):
            thread.join(timeout=_BACKGROUND_JOIN_TIMEOUT_SECONDS)

        with self._lock:
            current_task = self._background_tasks.get(action.task_id)
            if current_task is None:
                return self._unknown_task_observation(action)
            status = current_task.status

        if status == DelegateTaskStatus.CANCELLED:
            text = f"Background task {action.task_id} was cancelled"
            is_error = False
        else:
            text = (
                f"Stop requested for background task {action.task_id}, but it "
                f"remains {status.value} after the cleanup deadline"
            )
            is_error = True
        return DelegateObservation.from_text(
            text=text,
            command=action.command,
            task_id=action.task_id,
            agent_id=agent_id,
            status=status,
            is_error=is_error,
        )

    @staticmethod
    def _missing_task_id_observation(
        action: "DelegateAction",
    ) -> DelegateObservation:
        return DelegateObservation.from_text(
            text=f"task_id is required for {action.command} action",
            command=action.command,
            is_error=True,
        )

    def _unknown_task_observation(
        self,
        action: "DelegateAction",
    ) -> DelegateObservation:
        available = ", ".join(sorted(self._background_tasks)) or "none"
        return DelegateObservation.from_text(
            text=(
                f"Background task '{action.task_id}' not found. "
                f"Available task IDs: {available}"
            ),
            command=action.command,
            task_id=action.task_id,
            is_error=True,
        )
