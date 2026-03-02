"""Write file tool definition (Gemini-style)."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, PrivateAttr
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


class WriteFileAction(Action):
    """Schema for write file operation."""

    file_path: str = Field(description="The path to the file to write to.")
    content: str = Field(description="The content to write to the file.")


class WriteFileObservation(Observation):
    """Observation from writing a file."""

    file_path: str | None = Field(
        default=None, description="The file path that was written."
    )
    is_new_file: bool = Field(
        default=False, description="Whether a new file was created."
    )
    old_content: str | None = Field(
        default=None, description="The previous content of the file (if it existed)."
    )
    new_content: str | None = Field(
        default=None, description="The new content written to the file."
    )

    _diff_cache: Text | None = PrivateAttr(default=None)

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")
            return super().visualize

        if self.file_path:
            if self.is_new_file:
                text.append("✨ ", style="green bold")
                text.append(f"Created: {self.file_path}\n", style="green")
            else:
                text.append("✏️  ", style="yellow bold")
                text.append(f"Updated: {self.file_path}\n", style="yellow")

            if self.old_content is not None and self.new_content is not None:
                from openhands.tools.file_editor.utils.diff import visualize_diff

                if not self._diff_cache:
                    self._diff_cache = visualize_diff(
                        self.file_path,
                        self.old_content,
                        self.new_content,
                        n_context_lines=2,
                        change_applied=True,
                    )
                text.append(self._diff_cache)
        return text


class WriteFileTool(ToolDefinition[WriteFileAction, WriteFileObservation]):
    """Tool for writing complete file contents."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["WriteFileTool"]:
        """Initialize WriteFileTool with executor.

        Args:
            conv_state: Conversation state to get working directory from.
        """
        from openhands.tools.gemini.write_file.impl import WriteFileExecutor

        executor = WriteFileExecutor(workspace_root=conv_state.workspace.working_dir)

        working_dir = conv_state.workspace.working_dir
        tool_description = render_template(
            prompt_dir=str(PROMPT_DIR),
            template_name="tool_description.j2",
            working_dir=working_dir,
        )

        return [
            cls(
                action_type=WriteFileAction,
                observation_type=WriteFileObservation,
                description=tool_description,
                annotations=ToolAnnotations(
                    title="write_file",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


register_tool(WriteFileTool.name, WriteFileTool)
