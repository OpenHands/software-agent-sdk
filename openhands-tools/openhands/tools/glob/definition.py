"""Glob tool implementation for fast file pattern matching."""

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


class GlobAction(Action):
    """Schema for glob pattern matching operations."""

    pattern: str = Field(
        description='The glob pattern to match files (e.g., "**/*.js", "src/**/*.ts")'
    )
    path: str | None = Field(
        default=None,
        description=(
            "The directory (absolute path) to search in. "
            "Defaults to the current working directory."
        ),
    )


class GlobObservation(Observation):
    """Observation from glob pattern matching operations."""

    files: list[str] = Field(
        description="List of matching file paths sorted by modification time"
    )
    pattern: str = Field(description="The glob pattern that was used")
    search_path: str = Field(description="The directory that was searched")
    truncated: bool = Field(
        default=False, description="Whether results were truncated to 100 files"
    )


class GlobTool(ToolDefinition[GlobAction, GlobObservation]):
    """A ToolDefinition subclass that automatically initializes a GlobExecutor."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["GlobTool"]:
        """Initialize GlobTool with a GlobExecutor.

        Args:
            conv_state: Conversation state to get working directory from.
                         If provided, working_dir will be taken from
                         conv_state.workspace
        """
        # Import here to avoid circular imports
        from openhands.tools.glob.impl import GlobExecutor

        working_dir = conv_state.workspace.working_dir
        if not os.path.isdir(working_dir):
            raise ValueError(f"working_dir '{working_dir}' is not a valid directory")

        # Initialize the executor
        executor = GlobExecutor(working_dir=working_dir)

        tool_description = render_template(
            prompt_dir=str(PROMPT_DIR),
            template_name="tool_description.j2",
            working_dir=working_dir,
        )

        # Initialize the parent ToolDefinition with the executor
        return [
            cls(
                description=tool_description,
                action_type=GlobAction,
                observation_type=GlobObservation,
                annotations=ToolAnnotations(
                    title="glob",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# Automatically register the tool when this module is imported
register_tool(GlobTool.name, GlobTool)
