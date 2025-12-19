"""Read file tool definition (Gemini-style)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class ReadFileAction(Action):
    """Schema for read file operation."""

    file_path: str = Field(description="The path to the file to read.")
    offset: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional: The 0-based line number to start reading from. "
            "Use for paginating through large files."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Optional: Maximum number of lines to read. "
            "Use with 'offset' to paginate through large files."
        ),
    )


class ReadFileObservation(Observation):
    """Observation from reading a file."""

    file_path: str = Field(description="The file path that was read.")
    file_content: str = Field(default="", description="The content read from the file.")
    is_truncated: bool = Field(
        default=False,
        description="Whether the content was truncated due to size limits.",
    )
    lines_shown: tuple[int, int] | None = Field(
        default=None,
        description=(
            "If truncated, the range of lines shown (start, end) - 1-indexed."
        ),
    )
    total_lines: int | None = Field(
        default=None, description="Total number of lines in the file."
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")
            return super().visualize

        text.append("📄 ", style="blue bold")
        text.append(f"Read: {self.file_path}\n", style="blue")

        if self.is_truncated and self.lines_shown and self.total_lines:
            start, end = self.lines_shown
            text.append(
                (
                    f"⚠️  Content truncated: "
                    f"Showing lines {start}-{end} of {self.total_lines}\n"
                ),
                style="yellow",
            )

        text.append(self.file_content)
        return text


TOOL_DESCRIPTION = """Reads and returns the content of a specified file.

If the file is large, the content will be truncated. The tool's response will
clearly indicate if truncation has occurred and will provide details on how to
read more of the file using the 'offset' and 'limit' parameters.

For text files, it can read specific line ranges.

Examples:
- Read entire file: read_file(file_path="/path/to/file.py")
- Read with pagination: read_file(file_path="/path/to/file.py", offset=100, limit=50)
"""

# Maximum lines to read in one call (to prevent overwhelming the context)
MAX_LINES_PER_READ = 1000


class ReadFileTool(ToolDefinition[ReadFileAction, ReadFileObservation]):
    """Tool for reading file contents with pagination support."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["ReadFileTool"]:
        """Initialize ReadFileTool with executor.

        Args:
            conv_state: Conversation state to get working directory from.
        """
        from openhands.tools.gemini.read_file.impl import ReadFileExecutor

        executor = ReadFileExecutor(workspace_root=conv_state.workspace.working_dir)

        working_dir = conv_state.workspace.working_dir
        enhanced_description = (
            f"{TOOL_DESCRIPTION}\n\n"
            f"Your current working directory is: {working_dir}\n"
            f"File paths can be absolute or relative to this directory."
        )

        return [
            cls(
                action_type=ReadFileAction,
                observation_type=ReadFileObservation,
                description=enhanced_description,
                annotations=ToolAnnotations(
                    title="read_file",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


register_tool(ReadFileTool.name, ReadFileTool)
