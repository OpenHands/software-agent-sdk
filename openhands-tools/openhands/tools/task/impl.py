"""Task tool executor.

This module contains the TaskExecutor bridge shared by the task,
task_output, and task_stop tools.
"""

import threading

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.task.definition import (
    TaskAction,
    TaskObservation,
    TaskOutputAction,
    TaskOutputObservation,
    TaskStopAction,
    TaskStopObservation,
)
from openhands.tools.task.manager import TaskManager, TaskStatus


logger = get_logger(__name__)


TaskLifecycleAction = TaskAction | TaskOutputAction | TaskStopAction
TaskLifecycleObservation = TaskObservation | TaskOutputObservation | TaskStopObservation


class TaskExecutor(ToolExecutor[TaskLifecycleAction, TaskLifecycleObservation]):
    """Executor shared by all tools backed by one TaskManager."""

    def __init__(self, manager: TaskManager):
        self._manager = manager
        self._close_lock = threading.RLock()
        self._closing = False
        self._closed = False

    def __call__(
        self,
        action: TaskLifecycleAction,
        conversation: LocalConversation | None = None,
    ) -> TaskLifecycleObservation:
        if isinstance(action, TaskOutputAction):
            return self._get_output(action, conversation)
        if isinstance(action, TaskStopAction):
            return self._stop_task(action, conversation)
        return self._start_task(action, conversation)

    def _start_task(
        self,
        action: TaskAction,
        conversation: LocalConversation | None,
    ) -> TaskObservation:
        try:
            task = self._manager.start_task(
                prompt=action.prompt,
                subagent_type=action.subagent_type,
                description=action.description,
                resume=action.resume,
                conversation=conversation,
                run_in_background=action.run_in_background,
            )
            match task.status:
                case TaskStatus.COMPLETED:
                    return TaskObservation.from_text(
                        text=task.result or "Task completed with no result.",
                        task_id=task.id,
                        subagent=action.subagent_type,
                        status=task.status,
                    )
                case TaskStatus.ERROR:
                    return TaskObservation.from_text(
                        text=task.error or "Task failed.",
                        task_id=task.id,
                        subagent=action.subagent_type,
                        status=task.status,
                        is_error=True,
                    )
                case TaskStatus.CANCELLED:
                    return TaskObservation.from_text(
                        text=task.result or "Task was cancelled.",
                        task_id=task.id,
                        subagent=action.subagent_type,
                        status=task.status,
                        is_error=True,
                    )
                case TaskStatus.QUEUED | TaskStatus.RUNNING:
                    return TaskObservation.from_text(
                        text=(
                            "Task started in the background. Use task_output "
                            "to inspect progress or retrieve its result."
                        ),
                        task_id=task.id,
                        subagent=action.subagent_type,
                        status=task.status,
                    )
        except Exception as e:
            logger.error(f"Task execution failed: {e}", exc_info=True)
            return TaskObservation.from_text(
                text=f"Failed to execute task: {str(e)}",
                task_id="unknown",
                subagent=action.subagent_type,
                status="error",
                is_error=True,
            )

    def _get_output(
        self,
        action: TaskOutputAction,
        conversation: LocalConversation | None = None,
    ) -> TaskOutputObservation:
        try:
            if conversation is not None:
                self._manager._validate_parent(conversation)
            task = self._manager.get_task(
                task_id=action.task_id,
                block=action.block,
                timeout=action.timeout,
            )
            match task.status:
                case TaskStatus.COMPLETED:
                    text = task.result or "Task completed with no result."
                    is_error = False
                case TaskStatus.ERROR:
                    text = task.error or "Task failed."
                    is_error = True
                case TaskStatus.CANCELLED:
                    text = task.result or "Task was cancelled."
                    is_error = True
                case TaskStatus.QUEUED | TaskStatus.RUNNING:
                    text = f"Task is {task.status.value}."
                    is_error = False
            return TaskOutputObservation.from_text(
                text=text,
                task_id=task.id,
                subagent=task.subagent,
                status=task.status,
                is_error=is_error,
            )
        except Exception as e:
            logger.warning("Task output failed: %s", e)
            return TaskOutputObservation.from_text(
                text=f"Failed to read task output: {e}",
                task_id=action.task_id,
                subagent="unknown",
                status=TaskStatus.ERROR,
                is_error=True,
            )

    def _stop_task(
        self,
        action: TaskStopAction,
        conversation: LocalConversation | None = None,
    ) -> TaskStopObservation:
        try:
            if conversation is not None:
                self._manager._validate_parent(conversation)
            task = self._manager.stop_task(action.task_id)
            if task.status == TaskStatus.CANCELLED:
                text = "Task was cancelled."
                is_error = False
            else:
                text = (
                    f"Stop requested, but task remains {task.status.value} "
                    "after the cleanup deadline."
                )
                is_error = True
            return TaskStopObservation.from_text(
                text=text,
                task_id=task.id,
                subagent=task.subagent,
                status=task.status,
                is_error=is_error,
            )
        except Exception as e:
            logger.warning("Task stop failed: %s", e)
            return TaskStopObservation.from_text(
                text=f"Failed to stop task: {e}",
                task_id=action.task_id,
                subagent="unknown",
                status=TaskStatus.ERROR,
                is_error=True,
            )

    def interrupt(self) -> None:
        self._manager.interrupt()

    def close(self) -> None:
        with self._close_lock:
            if self._closed or self._closing:
                return
            self._closing = True
            try:
                self._manager.close()
            except BaseException:
                self._closing = False
                raise
            self._closing = False
            self._closed = True
