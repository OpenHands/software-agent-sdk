"""Task lifecycle manager.

This module implements the core task orchestration layer.
The TaskManager class is responsible for creating, resuming,
and running sub-agent tasks. In other words, it handles
everything related to task management.

The conversation linked to a completed task is persisted in
a temporary directory, ensuring the state can be restored
if the task is resumed for further work later.
"""

import json
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
_INDEX_FILENAME: Final[str] = "task_index.json"


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

    def attach_parent(self, conversation: LocalConversation) -> None:
        """Attach the parent conversation used to create sub-agent tasks.

        Idempotent: if a parent conversation is already attached, subsequent
        calls with the same conversation have no effect. Calls with a different
        conversation are also ignored, but log a warning to surface potential
        programming errors where two subsystems try to register different parents.
        """
        if (
            self._parent_conversation is not None
            and self._parent_conversation is not conversation
        ):
            logger.warning(
                "attach_parent called with a different conversation; ignoring."
            )
        self._ensure_parent(conversation)

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
            # Rehydrate prior tasks so resume works across a process restart.
            self._load_index()

    @property
    def _index_path(self) -> Path | None:
        """Path to the persisted task index, or None when the parent conversation
        does not persist (the index would not survive a restart anyway). Derived
        the same way as close(), so there is no separate state to keep in sync."""
        parent = self._parent_conversation
        if self._persistence_dir is None or parent is None:
            return None
        if parent.state.persistence_dir is None:
            return None
        return self._persistence_dir / _INDEX_FILENAME

    def _save_index(self) -> None:
        """Persist the task_id -> conversation_id index so resume survives a
        process restart. No-op when the parent does not persist."""
        index_path = self._index_path
        if index_path is None:
            return
        with self._tasks_lock:
            payload = [task.model_dump(mode="json") for task in self._tasks.values()]
        tmp_path = index_path.parent / f"{index_path.name}.tmp"
        try:
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            tmp_path.replace(index_path)  # atomic on the same filesystem
        except OSError as e:
            logger.warning(f"Failed to persist task index: {e}")

    def _load_index(self) -> None:
        """Rehydrate the task index from disk. Conversations are reconstructed
        lazily on resume; entries load with conversation=None. Tolerates a
        missing or unreadable index."""
        index_path = self._index_path
        if index_path is None or not index_path.exists():
            return
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"Failed to read task index, ignoring: {e}")
            return
        with self._tasks_lock:
            for entry in payload:
                try:
                    task = Task.model_validate(entry)
                except ValidationError as e:
                    logger.warning(f"Skipping invalid task index entry: {e}")
                    continue
                # Do not clobber a live in-memory task with a disk snapshot.
                self._tasks.setdefault(task.id, task)

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
        # Persist the final status (completed/error) for cross-process visibility.
        self._save_index()

    def start_task(
        self,
        prompt: str,
        subagent_type: str = "default",
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
            task = self._tasks[resume]

        self._save_index()
        return task

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

            task = Task(
                id=task_id,
                conversation_id=conversation_id,
                conversation=sub_conversation,
                status=TaskStatus.RUNNING,
            )
            self._tasks[task_id] = task

        self._save_index()
        return task

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
