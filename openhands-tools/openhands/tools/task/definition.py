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
from openhands.sdk.subagent import get_factory_info, get_registered_agent_definitions
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
    from openhands.tools.task.manager import ConfirmationHandler


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
] = """Launch a subagent to handle complex, multi-step tasks autonomously.

Subagents are autonomous agents that work independently and return results to you. They are your primary tool for understanding codebases and running
tests, but each delegation has overhead — use them when the task genuinely benefits from a separate agent, not for simple lookups.

Available agent types and the tools they have access to:
{agent_types_info}

When NOT to use the task tool:
- A single grep, find, or cat command would answer your question — just run it yourself
- You are making a file edit (use file_editor directly)
- You already have the context needed

When using the task tool:
- Write a detailed prompt describing exactly what you need
- Include specific file paths, class names, or error messages from the issue
- Tell the agent what to report back (file paths, line numbers, code snippets)
- The agent's results are authoritative — verify subagent results only when the task involves judgment or
  interpretation.
  
{task_tool_examples}
"""  # noqa: E501

TASK_TOOL_EXAMPLES: Final[dict[str, str]] = {
    "code-explorer": """
Example — Multi-step exploration (good use of code-explorer):
    subagent_type="code-explorer"
    prompt="Trace how the DateFormat.y() method is called through Django's
    template system. Find: (1) the method definition, (2) where it's
    registered as a format character, (3) all test cases. Include code
    snippets and file paths."
""",
    "bash-runner": """
Example — Running tests (good use of bash-runner):
    subagent_type="bash-runner"
    prompt="Run: cd /workspace/django && python tests/runtests.py
    utils_tests.test_dateformat -v 2. Report the full output."
""",
    "web researcher": """
Example — Research information on a website (good use of web researcher):
    subagent_type="web researcher"
    prompt="Navigate to the Stripe API docs and find the parameters for the PaymentIntent create endpoint."
""",  # noqa: E501
    "general purpose": """
Example — Perform a multi-step task involving code editing and shell commands:
    subagent_type="general purpose"
    prompt="Read the database module in src/db.py, extract the connection
    pooling logic into a separate file, update all imports, and run the
    test suite to verify nothing breaks."
""",
}


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
        confirmation_handler: "ConfirmationHandler | None" = None,
    ) -> list[ToolDefinition]:
        """Create the task tool.

        Args:
            conv_state: Conversation state for workspace info.
            confirmation_handler: Optional callback invoked when a sub-agent's
                confirmation policy requires user approval.  Receives
                `(task_id, pending_actions)` and must return `True` to
                approve or `False` to reject.

        Returns:
            List containing a single TaskTool.
        """
        from openhands.tools.task.impl import TaskExecutor, TaskManager

        agent_types_info = get_factory_info()

        registered = {d.name for d in get_registered_agent_definitions()}
        task_tool_examples = "\n".join(
            ex for name, ex in TASK_TOOL_EXAMPLES.items() if name in registered
        )

        task_description = TASK_TOOL_DESCRIPTION.format(
            agent_types_info=agent_types_info,
            task_tool_examples=task_tool_examples,
        )

        manager = TaskManager(confirmation_handler=confirmation_handler)
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
