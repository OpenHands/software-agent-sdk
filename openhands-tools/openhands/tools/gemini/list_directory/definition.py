"""List directory tool definition (Gemini-style)."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
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

PROMPT_DIR = Path(__file__).parent / "templates"


class FileEntry(BaseModel):
    """Information about a file or directory."""

    name: str = Field(description="Name of the file or directory")
    path: str = Field(description="Absolute path to the file or directory")
    is_directory: bool = Field(description="Whether this entry is a directory")
    size: int = Field(description="Size of the file in bytes (0 for directories)")
    modified_time: datetime = Field(description="Last modified timestamp")


class ListDirectoryAction(Action):
    """Schema for list directory operation."""

    dir_path: str = Field(
        default=".",
        description="The path to the directory to list. Defaults to current directory.",
    )
    recursive: bool = Field(
        default=False,
        description="Whether to list subdirectories recursively (up to 2 levels).",
    )


class ListDirectoryObservation(Observation):
    """Observation from listing a directory."""

    dir_path: str | None = Field(
        default=None, description="The directory path that was listed."
    )
    entries: list[FileEntry] = Field(
        default_factory=list, description="List of files and directories found."
    )
    total_count: int = Field(default=0, description="Total number of entries found.")
    is_truncated: bool = Field(
        default=False,
        description="Whether the listing was truncated due to too many entries.",
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")
            return super().visualize

        if self.dir_path:
            text.append("📁 ", style="blue bold")
            text.append(f"Directory: {self.dir_path}\n", style="blue")

            if self.total_count == 0:
                text.append("(empty directory)\n", style="dim")
            else:
                # Build a simple text-based table
                lines = []
                lines.append(f"{'Type':<6} {'Name':<40} {'Size':>10} {'Modified':<16}")
                lines.append("-" * 76)

                for entry in self.entries[:50]:
                    entry_type = "📁" if entry.is_directory else "📄"
                    size_str = (
                        "-" if entry.is_directory else self._format_size(entry.size)
                    )
                    modified_str = entry.modified_time.strftime("%Y-%m-%d %H:%M")
                    # Truncate name if too long
                    name = (
                        entry.name[:38] + ".." if len(entry.name) > 40 else entry.name
                    )
                    lines.append(
                        f"{entry_type:<6} {name:<40} {size_str:>10} {modified_str:<16}"
                    )

                text.append("\n".join(lines) + "\n")

                if self.is_truncated:
                    text.append(
                        f"\n⚠️  Showing first 50 of {self.total_count} entries\n",
                        style="yellow",
                    )

        return text

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        size_float = float(size)
        for unit in ["B", "KB", "MB", "GB"]:
            if size_float < 1024.0:
                return f"{size_float:.1f}{unit}"
            size_float /= 1024.0
        return f"{size_float:.1f}TB"


# Maximum entries to return (to prevent overwhelming the context)
MAX_ENTRIES = 500


class ListDirectoryTool(ToolDefinition[ListDirectoryAction, ListDirectoryObservation]):
    """Tool for listing directory contents with metadata."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["ListDirectoryTool"]:
        """Initialize ListDirectoryTool with executor.

        Args:
            conv_state: Conversation state to get working directory from.
        """
        from openhands.tools.gemini.list_directory.impl import ListDirectoryExecutor

        executor = ListDirectoryExecutor(
            workspace_root=conv_state.workspace.working_dir
        )

        working_dir = conv_state.workspace.working_dir
        tool_description = render_template(
            prompt_dir=str(PROMPT_DIR),
            template_name="tool_description.j2",
            working_dir=working_dir,
        )

        return [
            cls(
                action_type=ListDirectoryAction,
                observation_type=ListDirectoryObservation,
                description=tool_description,
                annotations=ToolAnnotations(
                    title="list_directory",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


register_tool(ListDirectoryTool.name, ListDirectoryTool)
