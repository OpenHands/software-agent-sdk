"""Delegate tool definitions for OpenHands agents."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


CommandLiteral = Literal["spawn", "delegate"]


class DelegateAction(Action):
    """Schema for delegation operations."""

    command: CommandLiteral = Field(
        description="The commands to run. Allowed options are: `spawn`, `delegate`."
    )
    ids: list[str] | None = Field(
        default=None,
        description="Required parameter of `spawn` command. "
        "List of identifiers to initialize sub-agents with.",
    )
    agent_types: list[str | None] | None = Field(
        default=None,
        description=(
            "Optional parameter of `spawn` command. "
            "List of agent types for each ID "
            "(e.g., ['researcher', 'programmer', 'default']). "
            "Use 'default' for default agent. Length must match ids if provided."
        ),
    )
    tasks: dict[str, str] | None = Field(
        default=None,
        description=(
            "Required parameter of `delegate` command. "
            "Dictionary mapping sub-agent identifiers to task descriptions."
        ),
    )


class DelegateObservation(Observation):
    """Observation from delegation operations."""

    command: CommandLiteral = Field(description="The command that was executed")


class DelegateTool(ToolDefinition[DelegateAction, DelegateObservation]):
    """A ToolDefinition subclass that automatically initializes a DelegateExecutor."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
        max_children: int = 5,
    ) -> Sequence["DelegateTool"]:
        """Initialize DelegateTool with a DelegateExecutor.

        Args:
            conv_state: Conversation state (used to get workspace location)
            max_children: Maximum number of concurrent sub-agents (default: 5)

        Returns:
            List containing a single delegate tool definition
        """
        # Import here to avoid circular imports
        from openhands.tools.delegate.impl import DelegateExecutor
        from openhands.tools.delegate.registration import get_factory_info

        # Get agent info
        agent_types_info = get_factory_info()

        # Create dynamic description with workspace and agent type info
        workspace_path = conv_state.workspace.working_dir
        tool_description = f"""Delegation tool for spawning sub-agents and delegating tasks to them.

This tool provides two commands:

**spawn**: Initialize sub-agents with meaningful identifiers and optional types
- Use descriptive identifiers that make sense for your use case (e.g., 'refactoring', 'run_tests', 'research')
- Optionally specify agent types for specialized capabilities
- Each identifier creates a separate sub-agent conversation
- Examples:
  - Default agents: {{"command": "spawn", "ids": ["research", "implementation"]}}
  - Specialized agents: {{"command": "spawn", "ids": ["research", "code"], "agent_types": ["researcher", "programmer"]}}
  - Mixed types: {{"command": "spawn", "ids": ["research", "generic"], "agent_types": ["researcher", null]}}

**delegate**: Send tasks to specific sub-agents and wait for results
- Use a dictionary mapping sub-agent identifiers to task descriptions
- This is a blocking operation - waits for all sub-agents to complete
- Returns a single observation containing results from all sub-agents
- Example: {{"command": "delegate", "tasks": {{"research": "Find best practices for async code", "implementation": "Refactor the MyClass class"}}}}

{agent_types_info}

**Important Notes:**
- Identifiers used in delegate must match those used in spawn
- All operations are blocking and return comprehensive results
- Sub-agents work in the same workspace as the main agent: {workspace_path}
"""  # noqa

        # Initialize the executor without parent conversation
        # (will be set on first call)
        executor = DelegateExecutor(max_children=max_children)

        # Initialize the parent Tool with the executor
        return [
            cls(
                action_type=DelegateAction,
                observation_type=DelegateObservation,
                description=tool_description,
                annotations=ToolAnnotations(
                    title="delegate",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]
