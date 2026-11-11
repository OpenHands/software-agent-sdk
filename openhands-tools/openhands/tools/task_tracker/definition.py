import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, get_args

from pydantic import BaseModel, Field, ValidationError, field_validator


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState

from rich.text import Text

from openhands.sdk.logger import get_logger
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


logger = get_logger(__name__)

# Type alias for task tracker status
TaskTrackerStatusType = Literal["todo", "in_progress", "done"]

# Models trained on other agents' todo tools send these names for our statuses.
_STATUS_ALIASES: dict[str, TaskTrackerStatusType] = {
    "pending": "todo",
    "to_do": "todo",
    "completed": "done",
    "complete": "done",
}


class TaskItem(BaseModel):
    title: str = Field(..., description="A brief title for the task.")
    notes: str = Field("", description="Additional details or notes about the task.")
    status: TaskTrackerStatusType = Field(
        "todo",
        description="The current status of the task. "
        "One of 'todo', 'in_progress', or 'done'.",
    )

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in get_args(TaskTrackerStatusType):
            return normalized
        return _STATUS_ALIASES.get(normalized, value)


class TaskTrackerAction(Action):
    """An action where the agent writes or updates a task list for task management."""

    command: Literal["view", "plan"] = Field(
        default="view",
        description="The command to execute. `view` shows the current task list. `plan` creates or updates the task list based on provided requirements and progress. Always `view` the current list before making changes.",  # noqa: E501
    )
    task_list: list[TaskItem] = Field(
        default_factory=list,
        description="The full task list. Required parameter of `plan` command.",
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation with task management styling."""
        content = Text()

        # Add command header with icon
        if self.command == "view":
            content.append("👀 ", style="blue")
            content.append("View Task List", style="blue")
        else:  # plan
            content.append("📋 ", style="green")
            content.append("Update Task List", style="green")

        # Show task count if planning
        if self.command == "plan" and self.task_list:
            content.append(f" ({len(self.task_list)} tasks)")

        return content


class TaskTrackerObservation(Observation):
    """This data class represents the result of a task tracking operation."""

    command: Literal["view", "plan"] = Field(
        description='The command that was executed: "view" or "plan".'
    )
    task_list: list[TaskItem] = Field(
        default_factory=list, description="The current task list"
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation with task list formatting."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")

        if self.task_list:
            # Count tasks by status
            todo_count = sum(1 for task in self.task_list if task.status == "todo")
            in_progress_count = sum(
                1 for task in self.task_list if task.status == "in_progress"
            )
            done_count = sum(1 for task in self.task_list if task.status == "done")

            # Show status summary
            if self.command == "plan":
                text.append("✅ ", style="green")
                text.append("Task list updated: ", style="green")
            else:  # view command
                text.append("📋 ", style="blue")
                text.append("Current task list: ", style="blue")

            # Status counts
            status_parts = []
            if todo_count:
                status_parts.append(f"{todo_count} todo")
            if in_progress_count:
                status_parts.append(f"{in_progress_count} in progress")
            if done_count:
                status_parts.append(f"{done_count} done")

            if status_parts:
                text.append(", ".join(status_parts), style="white")
                text.append("\n\n")

            # Show the actual task list
            for i, task in enumerate(self.task_list, 1):
                # Status icon
                if task.status == "done":
                    text.append("✅ ", style="green")
                elif task.status == "in_progress":
                    text.append("🔄 ", style="yellow")
                else:  # todo
                    text.append("⏳ ", style="blue")

                # Task title
                text.append(f"{i}. {task.title}", style="white")

                # NEW: show notes under the title if present
                if task.notes:
                    text.append("\n   Notes: " + task.notes, style="italic")

                if i < len(self.task_list):
                    text.append("\n")
        else:
            text.append("📝 ", style="blue")
            text.append("Task list is empty")

        return text


class TaskTrackerExecutor(ToolExecutor[TaskTrackerAction, TaskTrackerObservation]):
    """Executor for the task tracker tool."""

    save_dir: Path | None

    def __init__(self, save_dir: str | None = None):
        """Initialize TaskTrackerExecutor.

        Args:
            save_dir: Optional directory to save tasks to. If provided, tasks will be
                     persisted to save_dir/TASKS.md
        """
        self.save_dir = Path(save_dir) if save_dir else None
        logger.info(f"TaskTrackerExecutor initialized with save_dir: {self.save_dir}")
        self._task_list: list[TaskItem] = []

        # Load existing tasks if save_dir is provided and file exists
        if self.save_dir:
            self._load_tasks()

    def __call__(
        self,
        action: TaskTrackerAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> TaskTrackerObservation:
        """Execute the task tracker action."""
        if action.command == "plan":
            # Update the task list
            self._task_list = action.task_list
            # Save to file if save_dir is provided
            if self.save_dir:
                self._save_tasks()
            return TaskTrackerObservation.from_text(
                text=(
                    f"Task list has been updated with {len(self._task_list)} item(s)."
                ),
                command=action.command,
                task_list=self._task_list,
            )
        elif action.command == "view":
            # Return the current task list
            if not self._task_list:
                return TaskTrackerObservation.from_text(
                    text=('No task list found. Use the "plan" command to create one.'),
                    command=action.command,
                    task_list=[],
                )
            content = self._format_task_list(self._task_list)
            return TaskTrackerObservation.from_text(
                text=content,
                command=action.command,
                task_list=self._task_list,
            )
        else:
            return TaskTrackerObservation.from_text(
                text=(
                    f"Unknown command: {action.command}. "
                    'Supported commands are "view" and "plan".'
                ),
                is_error=True,
                command=action.command,
                task_list=[],
            )

    def _format_task_list(self, task_list: list[TaskItem]) -> str:
        """Format the task list for display."""
        if not task_list:
            return "No tasks in the list."

        content = "# Task List\n\n"
        for i, task in enumerate(task_list, 1):
            status_icon = {"todo": "⏳", "in_progress": "🔄", "done": "✅"}.get(
                task.status, "⏳"
            )

            title = task.title
            notes = task.notes

            content += f"{i}. {status_icon} {title}\n"
            if notes:
                content += f"   {notes}\n"
            content += "\n"

        return content.strip()

    def _load_tasks(self) -> None:
        """Load tasks from the TASKS.json file if it exists."""
        if not self.save_dir:
            return

        tasks_file = self.save_dir / "TASKS.json"
        if not tasks_file.exists():
            return

        try:
            with open(tasks_file, encoding="utf-8") as f:
                self._task_list = [TaskItem.model_validate(d) for d in json.load(f)]
        except (OSError, json.JSONDecodeError, TypeError, ValidationError) as e:
            logger.warning(
                f"Failed to load tasks from {tasks_file}: {e}. Starting with "
                "an empty task list."
            )
            self._task_list = []

    def _save_tasks(self) -> None:
        """Save tasks to the TASKS.json file."""
        if not self.save_dir:
            return

        tasks_file = self.save_dir / "TASKS.json"
        try:
            # Create the directory if it doesn't exist
            self.save_dir.mkdir(parents=True, exist_ok=True)

            with open(tasks_file, "w", encoding="utf-8") as f:
                json.dump([task.model_dump() for task in self._task_list], f, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save tasks to {tasks_file}: {e}")
            pass


TASK_TRACKER_DESCRIPTION = """Maintain a visible task list for complex, multi-phase work, multiple requirements, or an explicit request for a plan. Skip tracking for trivial, atomic, few-step tasks and informational discussion; implement those directly. When uncertain, favor tracking.

At project start, capture requirements as precise, actionable tasks and break complex work into manageable units. View the current list before editing; plan replaces the full list, so preserve relevant tasks and progress. Update the list as work progresses, remove obsolete tasks, and add discovered issues or dependencies.

Statuses: todo = not started; in_progress = active; done = fully achieved. Mark a task in_progress before starting, keep at most one task in_progress, and finish it before starting another. Mark it done promptly only when completely successful. Leave incomplete, blocked, or failing work in_progress; never mark done while tests fail, errors remain, implementation is partial, or required resources are unavailable."""  # noqa: E501


class TaskTrackerTool(ToolDefinition[TaskTrackerAction, TaskTrackerObservation]):
    """A ToolDefinition subclass that automatically initializes a TaskTrackerExecutor."""  # noqa: E501

    catalog_description: ClassVar[str] = (
        "Keep a running task list to organise multi-step work."
    )
    conversation_local: ClassVar[bool] = True

    @classmethod
    def create(cls, conv_state: "ConversationState") -> Sequence["TaskTrackerTool"]:
        """Initialize TaskTrackerTool with a TaskTrackerExecutor.

        Args:
            conv_state: Conversation state to get persistence directory from.
                         If provided, save_dir will be taken from
                         conv_state.persistence_dir
        """
        executor = TaskTrackerExecutor(save_dir=conv_state.persistence_dir)

        # Initialize the parent Tool with the executor
        return [
            cls(
                description=TASK_TRACKER_DESCRIPTION,
                action_type=TaskTrackerAction,
                observation_type=TaskTrackerObservation,
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# Automatically register the tool when this module is imported
register_tool(TaskTrackerTool.name, TaskTrackerTool)
