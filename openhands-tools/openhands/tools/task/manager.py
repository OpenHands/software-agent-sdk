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
import uuid
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.logger import get_logger
from openhands.tools.delegate.registration import get_agent_factory


logger = get_logger(__name__)


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

    def __init__(self):
        self._parent_conversation: LocalConversation | None = None

        self._tasks: dict[str, Task] = {}

        # tmp directory to save task to eventually resume it later
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="openhands_tasks_"))

    def _ensure_parent(self, conversation: LocalConversation) -> None:
        if self._parent_conversation is None:
            self._parent_conversation = conversation

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
        # evict in-memory conversation
        self._tasks[task.id] = task.model_copy(update={"conversation": None})

    def start_task(
        self,
        prompt: str,
        subagent_type: str = "default",
        resume: str | None = None,
        max_turns: int | None = None,
        description: str | None = None,
        conversation: LocalConversation | None = None,
    ) -> Task:
        """Start a blocking sub-agent task.

        Args:
            prompt: The task description for the sub-agent.
            subagent_type: Type of agent to use.
            resume: Task ID to resume (continues existing conversation).
            description: Short label for the task.
            max_turns: Maximum number of agent iterations.
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
                max_turns=max_turns,
            )

        return self._run_task(
            task=task,
            prompt=prompt,
        )

    def _resume_task(self, resume: str, subagent_type: str) -> Task:
        """Resume a sub-agent task."""
        if resume not in self._tasks:
            raise ValueError(
                f"Task '{resume}' not found. "
                f"Available tasks: {', '.join(sorted(self._tasks))}"
            )

        worker_agent = self._get_sub_agent(subagent_type)
        conversation_id = self._tasks[resume].conversation_id
        conversation = LocalConversation(
            agent=worker_agent,
            workspace=self.parent_conversation.state.workspace.working_dir,
            persistence_dir=self._tmp_dir,
            conversation_id=conversation_id,
            delete_on_close=False,
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
        max_turns: int | None,
    ) -> Task:
        """Create a fresh task."""
        task_id, conversation_id = self._generate_ids()
        worker_agent = self._get_sub_agent(subagent_type)
        sub_conversation = self._get_conversation(
            description=description,
            max_turns=max_turns,
            task_id=task_id,
            worker_agent=worker_agent,
            conversation_id=conversation_id,
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
        max_turns: int | None,
        task_id: str,
        conversation_id: uuid.UUID,
        worker_agent: Agent,
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
            persistence_dir=self._tmp_dir,
            conversation_id=conversation_id,
            max_iteration_per_run=max_turns or 500,
            delete_on_close=False,
        )

    def _get_sub_agent(self, subagent_type: str) -> Agent:
        """Return the subagent assigned to the task.

        Raises:
            ValueError: If the subagent type is invalid.
        """
        parent = self.parent_conversation
        parent_llm = parent.agent.llm

        factory = get_agent_factory(subagent_type)

        llm_updates: dict = {"stream": False}
        sub_agent_llm = parent_llm.model_copy(update=llm_updates)

        return factory.factory_func(sub_agent_llm)

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
            task.conversation.run()
            result = get_agent_final_response(task.conversation.state.events)
            task.set_result(result)
            logger.info(f"Task '{task.id}' completed.")
        except Exception as e:
            task.set_error(str(e))
            logger.warning(f"Task {task.id} failed with error: {e}")
        finally:
            self._evict_task(task)

        return task

    def close(self) -> None:
        """Clean up tmp directory and remove all created tasks."""
        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

        self._tasks.clear()
