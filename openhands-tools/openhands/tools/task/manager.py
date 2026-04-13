"""Task lifecycle manager.

This module implements the core task orchestration layer.
The TaskManager class is responsible for creating, resuming,
and running sub-agent tasks. In other words, it handles
everything related to task management.

The conversation linked to a completed task is persisted in
a temporary directory, ensuring the state can be restored
if the task is resumed for further work later.
"""

import shutil
import tempfile
import threading
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
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.security import ConfirmationPolicyBase
from openhands.sdk.subagent.registry import AgentFactory, get_agent_factory


if TYPE_CHECKING:
    from openhands.sdk.event import ActionEvent

ConfirmationHandler = Callable[[str, list["ActionEvent"]], bool]


logger = get_logger(__name__)

_SUBAGENTS_DIR: Final[str] = "subagents"


class TaskStatus(StrEnum):
    """Represents the lifecycle states of a task."""

    RUNNING = "running"
    """The task is currently being processed by an agent."""

    COMPLETED = "completed"
    """The task completed successfully and returned a valid result or response."""

    ERROR = "error"
    """The task failed to complete due to an unhandled exception or system fault."""


class Task(BaseModel):
    """Represents a task."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(description="Unique identifier of the task.")
    status: TaskStatus = Field(description="Task status.")
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

    def set_result(self, result: str) -> None:
        """Set task as successful."""
        self.result = result
        self.error = None
        self.status = TaskStatus.COMPLETED

    def set_error(self, error: str) -> None:
        """Set task as failed with an error."""
        self.error = error
        self.result = None
        self.status = TaskStatus.ERROR


class TaskManager:
    """Manage sub-agent tasks."""

    def __init__(
        self,
        confirmation_handler: ConfirmationHandler | None = None,
    ):
        self._parent_conversation: LocalConversation | None = None
        self._confirmation_handler = confirmation_handler

        self._tasks: dict[str, Task] = {}
        self._tasks_lock = threading.Lock()

        # Set once in _ensure_parent: uses the parent's subagents dir
        # when the parent persists, otherwise a temporary directory.
        self._persistence_dir: Path | None = None

    def _ensure_parent(self, conversation: LocalConversation) -> None:
        if self._parent_conversation is None:
            self._parent_conversation = conversation
            parent_persistence_dir = conversation.state.persistence_dir
            if parent_persistence_dir is not None:
                self._persistence_dir = Path(parent_persistence_dir) / _SUBAGENTS_DIR
                self._persistence_dir.mkdir(parents=True, exist_ok=True)
            else:
                self._persistence_dir = Path(
                    tempfile.mkdtemp(prefix="openhands_tasks_")
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
        task_number = len(self._tasks) + 1
        task_id = f"task_{task_number:08x}"
        uuid_ = uuid.uuid4()
        return task_id, uuid_

    def _evict_task(self, task: Task) -> None:
        if task.conversation:
            task.conversation.pause()
            task.conversation.close()
        with self._tasks_lock:
            self._tasks[task.id] = task.model_copy(update={"conversation": None})

    def start_task(
        self,
        prompt: str,
        subagent_type: str = "general purpose",
        resume: str | None = None,
        description: str | None = None,
        conversation: LocalConversation | None = None,
    ) -> Task:
        """Start a blocking sub-agent task.

        Args:
            prompt: The task description for the sub-agent.
            subagent_type: Type of agent to use.
            resume: Task ID to resume (continues existing conversation).
            description: Short label for the task.
            conversation: Parent conversation (set on first call).

        Returns:
            TaskState with the final result.
        """
        if conversation:
            self._ensure_parent(conversation)

        if resume:
            task = self._resume_task(
                resume=resume,
                subagent_type=subagent_type,
            )
        else:
            task = self._create_task(
                subagent_type=subagent_type,
                description=description,
            )

        return self._run_task(
            task=task,
            prompt=prompt,
        )

    def _resume_task(self, resume: str, subagent_type: str) -> Task:
        """Resume a sub-agent task."""
        with self._tasks_lock:
            if resume not in self._tasks:
                raise ValueError(
                    f"Task '{resume}' not found. "
                    f"Available tasks: {', '.join(sorted(self._tasks))}"
                )

            factory = get_agent_factory(subagent_type)
            worker_agent = self._get_sub_agent_from_factory(factory)
            conversation_id = self._tasks[resume].conversation_id
            conversation = LocalConversation(
                agent=worker_agent,
                workspace=self.parent_conversation.state.workspace.working_dir,
                persistence_dir=self._persistence_dir,
                conversation_id=conversation_id,
                hook_config=factory.definition.hooks,
                delete_on_close=True,
            )

            self._set_confirmation_policy(
                conversation,
                factory.definition.get_confirmation_policy(),
            )

            self._tasks[resume] = self._tasks[resume].model_copy(
                update={
                    "conversation": conversation,
                    "status": TaskStatus.RUNNING,
                }
            )

            return self._tasks[resume]

    def _create_task(
        self,
        subagent_type: str,
        description: str | None,
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

        with self._tasks_lock:
            task_id, conversation_id = self._generate_ids()

            sub_conversation = self._get_conversation(
                description=description,
                max_iteration_per_run=effective_max_iter,
                task_id=task_id,
                worker_agent=worker_agent,
                conversation_id=conversation_id,
                hook_config=factory.definition.hooks,
            )

            self._set_confirmation_policy(
                sub_conversation,
                factory.definition.get_confirmation_policy(),
            )

            self._tasks[task_id] = Task(
                id=task_id,
                conversation_id=conversation_id,
                conversation=sub_conversation,
                status=TaskStatus.RUNNING,
            )
            return self._tasks[task_id]

    def _get_conversation(
        self,
        description: str | None,
        max_iteration_per_run: int,
        task_id: str,
        conversation_id: uuid.UUID,
        worker_agent: Agent,
        hook_config: HookConfig | None = None,
    ) -> LocalConversation:
        parent = self.parent_conversation
        parent_visualizer = parent._visualizer

        visualizer = None
        if parent_visualizer is not None:
            label = description or task_id
            visualizer = parent_visualizer.create_sub_visualizer(label)

        return LocalConversation(
            agent=worker_agent,
            workspace=parent.state.workspace.working_dir,
            visualizer=visualizer,
            persistence_dir=self._persistence_dir,
            conversation_id=conversation_id,
            max_iteration_per_run=max_iteration_per_run,
            hook_config=hook_config,
            delete_on_close=True,
        )

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
            task.conversation.send_message(prompt, sender=parent_name)
            self._run_until_finished(task.id, task.conversation)

            max_iter_detail = self._get_max_iterations_error(task.conversation)
            if max_iter_detail is not None:
                result = get_agent_final_response(task.conversation.state.events)
                task.set_error(
                    f"Sub-agent was interrupted: {max_iter_detail}\n"
                    f"The task can be resumed with a fresh iteration budget "
                    f'by passing resume="{task.id}".\n'
                    f"Last response:\n{result}"
                )
                logger.warning(f"Task '{task.id}' hit max iterations limit.")
            elif (
                task.conversation.state.execution_status
                == ConversationExecutionStatus.ERROR
            ):
                result = get_agent_final_response(task.conversation.state.events)
                task.set_error(result or "Sub-agent encountered an error.")
                logger.warning(f"Task '{task.id}' ended with error status.")
            else:
                result = get_agent_final_response(task.conversation.state.events)
                task.set_result(result)
                logger.info(f"Task '{task.id}' completed.")
        except Exception as e:
            task.set_error(str(e))
            logger.warning(f"Task {task.id} failed with error: {e}")
        finally:
            self._update_parent_metrics(parent, task)
            self._evict_task(task)

        return task

    def _get_max_iterations_error(self, conversation: LocalConversation) -> str | None:
        """
        Return the error detail if the conversation
        hit MaxIterationsReached, else None.
        """
        from openhands.sdk.event.conversation_error import ConversationErrorEvent

        for event in reversed(list(conversation.state.events)):
            if (
                isinstance(event, ConversationErrorEvent)
                and event.code == "MaxIterationsReached"
            ):
                return event.detail
        return None

    def _run_until_finished(
        self, task_id: str, conversation: LocalConversation
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
                task_id, pending
            ):
                conversation.run()
            else:
                conversation.reject_pending_actions("User rejected the actions")
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
        if task.conversation is not None:
            parent.conversation_stats.usage_to_metrics[f"task:{task.id}"] = (
                task.conversation.conversation_stats.get_combined_metrics()
            )

    def close(self) -> None:
        """Clean up temporary directory (if used) and remove all created tasks."""
        # Only clean up when using a temp dir (parent had no persistence).
        # When the parent persists, subagent data lives under its directory.
        parent_persists = (
            self._parent_conversation is not None
            and self._parent_conversation.state.persistence_dir is not None
        )
        if (
            not parent_persists
            and self._persistence_dir is not None
            and self._persistence_dir.exists()
        ):
            shutil.rmtree(self._persistence_dir, ignore_errors=True)

        with self._tasks_lock:
            self._tasks.clear()
