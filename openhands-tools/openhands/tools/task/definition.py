"""Task tool definitions and registration.

This module defines the schema and tool classes for sub-agent task
delegation. It contains:
- the action/observation models (TaskAction, TaskObservation) for the TaskTool
- the tool description for the TaskTool

Moreover, it registers the two tool classes TaskTool (the individual tool)
and TaskToolSet (the entry-point that wires up a TaskManager-backed executor).
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from pydantic import Field
from rich.text import Text

from openhands.sdk import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.task.impl import TaskExecutor


class TaskAction(Action):
    """Schema for launching a sub-agent task."""

    description: str | None = Field(
        default=None,
        description="A short (3-5 word) description of the task.",
    )
    prompt: str = Field(
        description="The task for the agent to perform.",
    )
    subagent_type: str = Field(
        default="default",
        description="The type of specialized agent to use for this task.",
    )
    resume: str | None = Field(
        default=None,
        description="Task ID of the task to resume from.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Maximum number of agentic turns before stopping.",
        ge=1,
    )


class TaskObservation(Observation):
    """Observation from a task execution."""

    task_id: str = Field(description="The unique identifier of the task.")
    subagent: str = Field(description="The subagent of the task.")
    status: str = Field(description="The status of the task.")

    def _get_task_info(self) -> str:
        return (
            f"Task ID: {self.task_id}\nSubagent: {self.subagent}\nStatus: {self.status}"
        )

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append(self._get_task_info(), style="blue")
        text.append("\n")

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")

        text.append(self.text)
        return text

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """
        Default content formatting for converting observation to LLM readable content.
        Subclasses can override to provide richer content (e.g., images, diffs).
        """
        llm_content: list[TextContent | ImageContent] = [
            TextContent(text=self._get_task_info())
        ]

        # If is_error is true, prepend error message
        if self.is_error:
            llm_content.append(TextContent(text=self.ERROR_MESSAGE_HEADER))

        # Add content (now always a list)
        llm_content.extend(self.content)

        return llm_content


TASK_TOOL_DESCRIPTION: Final[
    str
] = """Launch a new agent to handle complex, multi-step tasks autonomously.

The task tool launches specialized agents that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

Available agent types and the tools they have access to:
{agent_types_info}

When using the task tool, you must specify a subagent_type parameter to select which agent type to use.

When NOT to use the task tool:
- If you want to read a specific file path, use the terminal tool instead of the task tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the terminal tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the terminal tool instead of the task tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above

Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you
should send a text message back to the user with a concise summary of the result.
- Agents can be resumed using the resume parameter by passing the task ID from a previous invocation. When resumed, the agent continues with its full previous
context preserved. When NOT resuming, each invocation starts fresh and you should provide a detailed task description with all necessary context.
- When you launch an agent with a task using the Task tool, a task ID will be returned to you. You can use this ID to resume the agent later if needed for follow-up work.
- Provide clear, detailed prompts so the agent can work autonomously and return exactly the information you need.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's
intent
- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your
judgement.
"""  # noqa: E501


class TaskTool(ToolDefinition[TaskAction, TaskObservation]):
    """Tool for launching (blocking) sub-agent tasks."""

    @classmethod
    def create(
        cls,
        executor: "TaskExecutor",
        description: str,
    ) -> Sequence["TaskTool"]:
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


class TaskToolSet(ToolDefinition[TaskAction, TaskObservation]):
    """Task tool set.

    Creates the Task tool backed by a shared TaskManager.

    Usage:
        from openhands.tools.task import TaskToolSet

        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
                Tool(name=TaskToolSet.name),
            ],
        )
    """

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",  # noqa: ARG003
    ) -> list[ToolDefinition]:
        """Create the task tool.

        Args:
            conv_state: Conversation state for workspace info.

        Returns:
            List containing a single TaskTool.
        """
        from openhands.tools.delegate.registration import get_factory_info
        from openhands.tools.task.impl import TaskExecutor, TaskManager

        full_agent_types_info = get_factory_info()
        lines = full_agent_types_info.splitlines()
        agent_types_info = "\n".join(lines[1:])

        task_description = TASK_TOOL_DESCRIPTION.format(
            agent_types_info=agent_types_info
        )

        manager = TaskManager()
        task_executor = TaskExecutor(manager=manager)

        tools: list[ToolDefinition] = []
        tools.extend(
            TaskTool.create(
                executor=task_executor,
                description=task_description,
            )
        )
        return tools


# Automatically register when this module is imported
register_tool(TaskToolSet.name, TaskToolSet)
register_tool(TaskTool.name, TaskTool)
