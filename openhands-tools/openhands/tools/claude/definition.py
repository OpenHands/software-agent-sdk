"""Claude Code-style delegation tool definitions.

This module defines the Task, TaskOutput, and TaskStop tools that match
Claude Code's delegation API. Under the hood, these tools use the existing
OpenHands delegation infrastructure (agent factories, LocalConversation, etc.).

The ClaudeDelegationToolSet is registered as a single tool spec that creates
all three tools sharing a ClaudeDelegationManager for coordinated state.
This follows the same pattern as BrowserToolSet.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import Field
from rich.text import Text

from openhands.sdk.context.prompts import render_template
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.claude.impl import (
        TaskExecutor,
        TaskOutputExecutor,
        TaskStopExecutor,
    )


_PROMPT_DIR: Final[Path] = Path(__file__).parent / "templates"


class TaskAction(Action):
    """Schema for launching a sub-agent task."""

    description: str | None = Field(
        default=None,
        description=("A short (3-5 word) description of the task."),
    )
    prompt: str = Field(
        description="The task for the agent to perform.",
    )
    subagent_type: str = Field(
        default="default",
        description="The type of specialized agent to use for this task.",
    )
    model: str | None = Field(
        default=None,
        description="Optional model to use. If not specified, inherits from parent.",
    )
    resume: str | None = Field(
        default=None,
        description="Optional agent ID to resume from.",
    )
    run_in_background: bool = Field(
        default=False,
        description="Set to true to run in the background.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Maximum number of agentic turns before stopping.",
        ge=1,
    )


class TaskObservation(Observation):
    """Observation from a task execution."""

    task_id: str = Field(description="The unique identifier of the task.")
    status: str = Field(description="The status of the task.")

    @property
    def visualize(self) -> Text:
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")
            return super().visualize

        status_styles = {
            "completed": "green",
            "running": "yellow",
            "error": "red",
        }
        status_style = status_styles.get(self.status, "white")

        text.append("🤖 ", style="blue bold")
        text.append(f"Task {self.task_id} ", style="blue")
        text.append(f"[{self.status}]", style=status_style)
        text.append("\n")
        text.append(self.text)
        return text


class TaskTool(ToolDefinition[TaskAction, TaskObservation]):
    """Tool for launching sub-agent tasks (Claude Code style)."""

    @classmethod
    def create(
        cls,
        executor: "TaskExecutor",
        description: str,
    ) -> Sequence["TaskTool"]:
        """Create TaskTool with a shared executor.

        This is called by ClaudeDelegationToolSet.create(), not by the
        resolver directly.
        """
        return [
            cls(
                action_type=TaskAction,
                observation_type=TaskObservation,
                description=description,
                annotations=ToolAnnotations(
                    title="task",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


class TaskOutputAction(Action):
    """Schema for retrieving task output."""

    task_id: str = Field(
        description="The task ID to get output from.",
    )
    block: bool = Field(
        default=True,
        description="Whether to wait for completion.",
    )
    timeout: int = Field(
        default=30000,
        ge=0,
        le=600000,
        description="Max wait time in ms.",
    )


class TaskOutputObservation(Observation):
    """Observation from task output retrieval."""

    task_id: str = Field(description="The task ID.")
    status: str = Field(
        description=("The status of the task: 'completed', 'running', or 'error'."),
    )

    @property
    def visualize(self) -> Text:
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            return super().visualize

        status_styles = {
            "completed": "green",
            "running": "yellow",
            "error": "red",
        }
        status_style = status_styles.get(self.status, "white")

        text.append("📋 ", style="blue bold")
        text.append(f"Task {self.task_id} ", style="blue")
        text.append(f"[{self.status}]", style=status_style)
        text.append("\n")
        text.append(self.text)
        return text


class TaskOutputTool(ToolDefinition[TaskOutputAction, TaskOutputObservation]):
    """Tool for retrieving background task output."""

    @classmethod
    def create(
        cls,
        executor: "TaskOutputExecutor",
    ) -> Sequence["TaskOutputTool"]:
        """Create TaskOutputTool with a shared executor.

        This is called by ClaudeDelegationToolSet.create(), not by the
        resolver directly.
        """

        return [
            cls(
                action_type=TaskOutputAction,
                observation_type=TaskOutputObservation,
                description=render_template(
                    prompt_dir=str(_PROMPT_DIR),
                    template_name="task_output_tool_description.j2",
                ),
                annotations=ToolAnnotations(
                    title="task_output",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class TaskStopAction(Action):
    """Schema for stopping a background task."""

    task_id: str = Field(
        description="The ID of the background task to stop.",
    )


class TaskStopObservation(Observation):
    """Observation from stopping a task."""

    task_id: str = Field(description="The task ID.")
    status: str = Field(
        description="The status after stop: 'stopped' or 'not_found'.",
    )

    @property
    def visualize(self) -> Text:
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            return super().visualize

        text.append("⏹️  ", style="red bold")
        text.append(f"Task {self.task_id} ", style="blue")
        text.append(f"[{self.status}]", style="yellow")
        return text


class TaskStopTool(ToolDefinition[TaskStopAction, TaskStopObservation]):
    """Tool for stopping background tasks."""

    @classmethod
    def create(
        cls,
        executor: "TaskStopExecutor",
    ) -> Sequence["TaskStopTool"]:
        """Create TaskStopTool with a shared executor.

        This is called by ClaudeDelegationToolSet.create(), not by the
        resolver directly.
        """
        return [
            cls(
                action_type=TaskStopAction,
                observation_type=TaskStopObservation,
                description=render_template(
                    prompt_dir=str(_PROMPT_DIR),
                    template_name="task_stop_tool_description.j2",
                ),
                annotations=ToolAnnotations(
                    title="task_stop",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class ClaudeDelegationToolSet(ToolDefinition[TaskAction, TaskObservation]):
    """Claude Code-style delegation tool set.

    Creates Task, TaskOutput, and TaskStop tools that share a
    ClaudeDelegationManager for coordinated sub-agent management.

    Usage:
        from openhands.tools.claude import CLAUDE_DELEGATION_TOOLS

        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
                *CLAUDE_DELEGATION_TOOLS,
            ],
        )
    """

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",  # noqa: ARG003
        max_children: int = 5,
    ) -> list[ToolDefinition]:
        """Create all Claude delegation tools with shared state.

        Args:
            conv_state: Conversation state for workspace info.
            max_children: Maximum number of concurrent sub-agent tasks.

        Returns:
            List of [TaskTool, TaskOutputTool, TaskStopTool] sharing a
            ClaudeDelegationManager.
        """
        from openhands.tools.claude.impl import (
            DelegationManager,
            TaskExecutor,
            TaskOutputExecutor,
            TaskStopExecutor,
        )
        from openhands.tools.delegate.registration import get_factory_info

        # Build dynamic description with agent type info
        # to be consistent with claude code description for the
        # tool, we remove the first line generated by the
        # `get_factory_info()` utility.
        full_agent_types_info = get_factory_info()
        lines = full_agent_types_info.splitlines()
        agent_types_info = "\n".join(lines[1:])

        task_description = render_template(
            prompt_dir=str(_PROMPT_DIR),
            template_name="task_tool_description.j2",
            agent_types_info=agent_types_info,
        )

        # Create a manager that will be shared between the 3 tools
        # the tools are actually a way for the main agent to
        # communicate with the manager
        manager = DelegationManager(max_children=max_children)
        task_executor = TaskExecutor(manager=manager)
        task_output_executor = TaskOutputExecutor(manager=manager)
        task_stop_executor = TaskStopExecutor(manager=manager)

        tools: list[ToolDefinition] = []
        tools.extend(
            TaskTool.create(
                executor=task_executor,
                description=task_description,
            )
        )
        tools.extend(TaskOutputTool.create(executor=task_output_executor))
        tools.extend(TaskStopTool.create(executor=task_stop_executor))

        return tools


# Automatically register when this module is imported
register_tool(ClaudeDelegationToolSet.name, ClaudeDelegationToolSet)
register_tool(TaskTool.name, TaskTool)
register_tool(TaskStopTool.name, TaskStopTool)
register_tool(TaskOutputTool.name, TaskOutputTool)
