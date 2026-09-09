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

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from openhands.sdk import Agent
from openhands.sdk.conversation import BaseConversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.types import TraceMetadataValue
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import detached_delegate_context
from openhands.sdk.security import ConfirmationPolicyBase
from openhands.sdk.subagent.registry import AgentFactory, get_agent_factory
from openhands.sdk.workspace import RemoteWorkspace
from openhands.tools.task.workspace import (
    SubagentWorkspace,
    SubagentWorkspaceFactory,
    SubagentWorkspaceReference,
    SubagentWorkspaceResolver,
    close_workspace,
    enter_workspace,
)


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
    conversation: BaseConversation | None = Field(
        default=None,
        exclude=True,
        description="Conversation state of the task.",
    )
    remote_workspace: RemoteWorkspace | None = Field(default=None, exclude=True)
    remote: bool = False
    subagent_type: str | None = None
    workspace_reference: SubagentWorkspaceReference | None = None

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
        workspace_factory: SubagentWorkspaceFactory | None = None,
        workspace_resolver: SubagentWorkspaceResolver | None = None,
    ):
        self._parent_conversation: LocalConversation | None = None
        self._confirmation_handler = confirmation_handler
        self._workspace_factory = workspace_factory
        self._workspace_resolver = workspace_resolver

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
            manifest = self._persistence_dir / "remote_tasks.json"
            if manifest.exists():
                tasks = TypeAdapter(list[Task]).validate_json(manifest.read_text())
                self._tasks.update({task.id: task for task in tasks})

    def _persist_remote_tasks(self) -> None:
        """Persist identities, never live workspace objects or credentials.

        Call while holding _tasks_lock.
        """
        assert self._persistence_dir is not None
        tasks = [task for task in self._tasks.values() if task.remote]
        if not tasks:
            return
        manifest = self._persistence_dir / "remote_tasks.json"
        temporary = manifest.with_suffix(".tmp")
        temporary.write_bytes(TypeAdapter(list[Task]).dump_json(tasks))
        temporary.replace(manifest)

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
        while task_id in self._tasks:
            task_number += 1
            task_id = f"task_{task_number:08x}"
        uuid_ = uuid.uuid4()
        return task_id, uuid_

    def _evict_task(self, task: Task) -> None:
        if task.remote_workspace is not None:
            # Retain the remote session and workspace until resume or parent close.
            with self._tasks_lock:
                self._persist_remote_tasks()
            return
        if task.conversation:
            task.conversation.pause()
            task.conversation.close()
        with self._tasks_lock:
            self._tasks[task.id] = task.model_copy(update={"conversation": None})

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

            stored_task = self._tasks[resume]
            if stored_task.remote and stored_task.subagent_type is not None:
                subagent_type = stored_task.subagent_type
            factory = get_agent_factory(subagent_type)
            worker_agent = self._get_sub_agent_from_factory(factory)
            conversation_id = self._tasks[resume].conversation_id
            workspace = self._tasks[resume].remote_workspace
            if stored_task.remote and workspace is None:
                if stored_task.workspace_reference is None:
                    raise ValueError(
                        f"Remote task '{resume}' has no persisted workspace identity; "
                        "cannot reconnect safely."
                    )
                if self._workspace_resolver is None:
                    raise ValueError(
                        f"Remote task '{resume}' requires a workspace_resolver "
                        "to reconnect to the original workspace."
                    )
                workspace = self._workspace_resolver(stored_task.workspace_reference)
                if workspace is None:
                    raise ValueError(f"Remote workspace for task '{resume}' is gone")
                if workspace is self.parent_conversation.state.workspace or any(
                    task.remote_workspace is workspace for task in self._tasks.values()
                ):
                    raise ValueError("Each subagent must own a distinct workspace")
                enter_workspace(workspace)
                if workspace.working_dir != stored_task.workspace_reference.working_dir:
                    close_workspace(workspace)
                    raise ValueError(
                        "Resolved workspace has a different working directory"
                    )
                stored_task.remote_workspace = workspace
            with detached_delegate_context() as link:
                conversation_class = (
                    LocalConversation if workspace is None else RemoteConversation
                )
                conversation_kwargs: dict = dict(
                    agent=worker_agent,
                    workspace=(
                        workspace
                        if workspace is not None
                        else self.parent_conversation.state.workspace.working_dir
                    ),
                    persistence_dir=self._persistence_dir,
                    conversation_id=conversation_id,
                    hook_config=factory.definition.hooks,
                    delete_on_close=True,
                    observability_metadata=self._delegate_observability_metadata(
                        task_id=resume, subagent_type=subagent_type, link=link
                    ),
                    observability_tags=["delegate"],
                    **({"require_existing": True} if workspace is not None else {}),
                )
                try:
                    conversation = conversation_class(**conversation_kwargs)
                except BaseException:
                    if workspace is not None and stored_task.conversation is None:
                        close_workspace(workspace)
                        stored_task.remote_workspace = None
                    raise

            try:
                self._set_confirmation_policy(
                    conversation,
                    factory.definition.get_confirmation_policy(),
                )
            except BaseException:
                try:
                    conversation.close()
                finally:
                    if workspace is not None and stored_task.conversation is None:
                        close_workspace(workspace)
                        stored_task.remote_workspace = None
                raise
            if workspace is not None:
                previous = self._tasks[resume].conversation
                if isinstance(previous, RemoteConversation):
                    previous.delete_on_close = False
                    previous.close()

            self._tasks[resume] = self._tasks[resume].model_copy(
                update={
                    "conversation": conversation,
                    "status": TaskStatus.RUNNING,
                }
            )
            self._persist_remote_tasks()

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
        # Sub-agent budget: definition value, else inherit the parent's.
        effective_max_budget = (
            factory.definition.max_budget_per_run
            or self.parent_conversation.max_budget_per_run
        )

        with self._tasks_lock:
            task_id, conversation_id = self._generate_ids()
            provisioned = (
                self._workspace_factory(task_id, subagent_type)
                if self._workspace_factory is not None
                else None
            )
            reference = (
                provisioned.reference
                if isinstance(provisioned, SubagentWorkspace)
                else None
            )
            workspace = (
                provisioned.workspace
                if isinstance(provisioned, SubagentWorkspace)
                else provisioned
            )
            if workspace is not None:
                if workspace is self.parent_conversation.state.workspace or any(
                    task.remote_workspace is workspace for task in self._tasks.values()
                ):
                    raise ValueError("Each subagent must own a distinct workspace")
                enter_workspace(workspace)

            sub_conversation = None
            try:
                if reference is not None and workspace is not None:
                    if reference.working_dir != workspace.working_dir:
                        raise ValueError(
                            "Workspace reference must describe the child's "
                            "working directory"
                        )
                sub_conversation = self._get_conversation(
                    description=description,
                    max_iteration_per_run=effective_max_iter,
                    max_budget_per_run=effective_max_budget,
                    task_id=task_id,
                    subagent_type=subagent_type,
                    worker_agent=worker_agent,
                    conversation_id=conversation_id,
                    hook_config=factory.definition.hooks,
                    remote_workspace=workspace,
                )

                self._set_confirmation_policy(
                    sub_conversation,
                    factory.definition.get_confirmation_policy(),
                )
            except BaseException:
                try:
                    if sub_conversation is not None:
                        sub_conversation.close()
                finally:
                    if workspace is not None:
                        close_workspace(workspace)
                raise

            self._tasks[task_id] = Task(
                id=task_id,
                conversation_id=conversation_id,
                conversation=sub_conversation,
                status=TaskStatus.RUNNING,
                remote_workspace=workspace,
                remote=workspace is not None,
                subagent_type=subagent_type,
                workspace_reference=reference,
            )
            self._persist_remote_tasks()
            return self._tasks[task_id]

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
        remote_workspace: RemoteWorkspace | None = None,
    ) -> BaseConversation:
        parent = self.parent_conversation
        parent_visualizer = parent._visualizer

        visualizer = None
        if parent_visualizer is not None:
            label = description or task_id
            visualizer = parent_visualizer.create_sub_visualizer(label)

        with detached_delegate_context() as link:
            conversation_class = (
                LocalConversation if remote_workspace is None else RemoteConversation
            )
            conversation_kwargs: dict = dict(
                agent=worker_agent,
                workspace=(
                    remote_workspace
                    if remote_workspace is not None
                    else parent.state.workspace.working_dir
                ),
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
            return conversation_class(**conversation_kwargs)

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
            task.conversation.send_message(prompt, sender=parent_name)
            self._run_until_finished(task.id, task.conversation)
            status = task.conversation.state.execution_status
            if status == ConversationExecutionStatus.FINISHED:
                result = get_agent_final_response(task.conversation.state.events)
                task.set_result(result)
                logger.info(f"Task '{task.id}' completed.")
            else:
                # Any non-FINISHED terminal status (run-limit, stuck, paused, ...)
                # is surfaced as an error, not an empty "completed"; the detail
                # keeps partial output so the parent can use/retry it.
                task.set_error(self._run_stop_detail(task.conversation, status))
                logger.warning(f"Task '{task.id}' stopped: status '{status.value}'.")
        except Exception as e:
            task.set_error(str(e))
            logger.warning(f"Task {task.id} failed with error: {e}")
        finally:
            try:
                self._update_parent_metrics(parent, task)
            except Exception:
                logger.warning("Failed to read subagent metrics", exc_info=True)
            finally:
                self._evict_task(task)

        return task

    @staticmethod
    def _run_stop_detail(
        conversation: BaseConversation,
        status: ConversationExecutionStatus,
    ) -> str:
        """Why a sub-agent stopped without finishing (run-limit, stuck, paused, ...),
        plus any partial output so the parent isn't left with nothing to use."""
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
        partial = get_agent_final_response(conversation.state.events)
        return f"{reason}\nPartial result:\n{partial}" if partial else reason

    def _run_until_finished(self, task_id: str, conversation: BaseConversation) -> None:
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
        conversation: BaseConversation,
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
        with self._tasks_lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            try:
                if task.conversation is not None:
                    task.conversation.close()
            except Exception:
                logger.warning("Failed to close subagent conversation", exc_info=True)
            finally:
                if task.remote_workspace is not None:
                    close_workspace(task.remote_workspace)
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
