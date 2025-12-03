"""Delegate tool definitions for OpenHands agents."""

import pathlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from openhands.sdk.context.prompts import render_template
from openhands.sdk.tool import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


PROMPT_DIR = pathlib.Path(__file__).parent / "templates"

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
    agent_types: list[str] | None = Field(
        default=None,
        description=(
            "Optional parameter of `spawn` command. "
            "List of agent types for each ID (e.g., ['researcher', 'programmer']). "
            "If omitted or blank for an ID, the default general-purpose agent is used."
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
        tool_description = render_template(
            prompt_dir=str(PROMPT_DIR),
            template_name="delegate_tool_description.j2",
            agent_types_info=agent_types_info,
            workspace_path=workspace_path,
        )

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


# Automatically register the tool when this module is imported
register_tool(DelegateTool.name, DelegateTool)
