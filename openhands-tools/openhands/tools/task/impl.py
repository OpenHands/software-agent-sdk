"""Task tool executor.

This module contains the TaskExecutor class,
which serves as a bridge between the tool interface
and the TaskManager. It translates a TaskAction into
a blocking sub-agent execution and returns a
TaskObservation containing either the task result or an error.
"""

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.task.definition import TaskAction, TaskObservation
from openhands.tools.task.manager import TaskManager, TaskStatus


logger = get_logger(__name__)


class TaskExecutor(ToolExecutor):
    """Executor for the Task tool (blocking only)."""

    def __init__(self, manager: TaskManager):
        self._manager = manager

    def __call__(
        self,
        action: TaskAction,
        conversation: LocalConversation | None = None,
    ) -> TaskObservation:
        try:
            task = self._manager.start_task(
                prompt=action.prompt,
                subagent_type=action.subagent_type,
                description=action.description,
                resume=action.resume,
                max_turns=action.max_turns,
                conversation=conversation,
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
                case _:
                    # this should never happen
                    raise RuntimeError(f"Unknown task status: {task.status}")
        except Exception as e:
            logger.error(f"Task execution failed: {e}", exc_info=True)
            return TaskObservation.from_text(
                text=f"Failed to execute task: {str(e)}",
                task_id="unknown",
                subagent=action.subagent_type,
                status="error",
                is_error=True,
            )

    def close(self) -> None:
        self._manager.close()
