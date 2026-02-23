"""Grep tool implementation for fast content search."""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

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

PROMPT_DIR = Path(__file__).parent / "templates"


class GrepAction(Action):
    """Schema for grep content search operations."""

    pattern: str = Field(description="The regex pattern to search for in file contents")
    path: str | None = Field(
        default=None,
        description=(
            "The directory (absolute path) to search in. "
            "Defaults to the current working directory."
        ),
    )
    include: str | None = Field(
        default=None,
        description=(
            "Optional file pattern to filter which files to search "
            '(e.g., "*.js", "*.{ts,tsx}")'
        ),
    )


class GrepObservation(Observation):
    """Observation from grep content search operations."""

    matches: list[str] = Field(description="List of file paths containing the pattern")
    pattern: str = Field(description="The regex pattern that was used")
    search_path: str = Field(description="The directory that was searched")
    include_pattern: str | None = Field(
        default=None, description="The file pattern filter that was used"
    )
    truncated: bool = Field(
        default=False, description="Whether results were truncated to 100 files"
    )


class GrepTool(ToolDefinition[GrepAction, GrepObservation]):
    """A ToolDefinition subclass that automatically initializes a GrepExecutor."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["GrepTool"]:
        """Initialize GrepTool with a GrepExecutor.

        Args:
            conv_state: Conversation state to get working directory from.
                         If provided, working_dir will be taken from
                         conv_state.workspace
        """
        # Import here to avoid circular imports
        from openhands.tools.grep.impl import GrepExecutor

        working_dir = conv_state.workspace.working_dir
        if not os.path.isdir(working_dir):
            raise ValueError(f"working_dir '{working_dir}' is not a valid directory")

        # Initialize the executor
        executor = GrepExecutor(working_dir=working_dir)

        tool_description = render_template(
            prompt_dir=str(PROMPT_DIR),
            template_name="tool_description.j2",
            working_dir=working_dir,
        )

        # Initialize the parent ToolDefinition with the executor
        return [
            cls(
                description=tool_description,
                action_type=GrepAction,
                observation_type=GrepObservation,
                annotations=ToolAnnotations(
                    title="grep",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# Automatically register the tool when this module is imported
register_tool(GrepTool.name, GrepTool)
