"""Edit tool definition (Gemini-style)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import Field, PrivateAttr
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


class EditAction(Action):
    """Schema for edit operation."""

    file_path: str = Field(description="The path to the file to modify.")
    old_string: str = Field(
        description=(
            "The text to replace. To create a new file, use an empty string. "
            "Must match the exact text in the file including whitespace."
        )
    )
    new_string: str = Field(description="The text to replace it with.")
    expected_replacements: int = Field(
        default=1,
        ge=0,
        description=(
            "Number of replacements expected. Defaults to 1. "
            "Use when you want to replace multiple occurrences. "
            "The edit will fail if the actual count doesn't match."
        ),
    )


class EditObservation(Observation):
    """Observation from editing a file."""

    file_path: str | None = Field(
        default=None, description="The file path that was edited."
    )
    is_new_file: bool = Field(
        default=False, description="Whether a new file was created."
    )
    replacements_made: int = Field(
        default=0, description="Number of replacements actually made."
    )
    old_content: str | None = Field(
        default=None, description="The content before the edit."
    )
    new_content: str | None = Field(
        default=None, description="The content after the edit."
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
                text.append(
                    (
                        f"Edited: {self.file_path} "
                        f"({self.replacements_made} replacement(s))\n"
                    ),
                    style="yellow",
                )

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


TOOL_DESCRIPTION = """Replaces text within a file.

By default, replaces a single occurrence, but can replace multiple occurrences
when `expected_replacements` is specified. The edit will fail if the actual
number of occurrences doesn't match the expected count.

This tool is useful for making targeted changes to files without rewriting
the entire content.

Key behaviors:
- To create a new file: use an empty string for `old_string`
- The `old_string` must match EXACTLY (including whitespace and indentation)
- If 0 occurrences are found, the edit fails with an error
- If the number of occurrences doesn't match `expected_replacements`, the edit fails
- If `old_string` equals `new_string`, no changes are made

Tips for success:
- Include enough context (3-5 lines) to make `old_string` unique
- Use the `read_file` tool first to verify the exact text to replace
- For large changes affecting many lines, consider `write_file` instead

Examples:
- Simple replacement: edit(file_path="test.py", old_string="old text", new_string="new text")
- Create file: edit(file_path="new.py", old_string="", new_string="print('hello')")
- Multiple replacements: edit(file_path="test.py", old_string="foo", new_string="bar", expected_replacements=3)
"""  # noqa: E501


class EditTool(ToolDefinition[EditAction, EditObservation]):
    """Tool for editing files via find/replace."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["EditTool"]:
        """Initialize EditTool with executor.

        Args:
            conv_state: Conversation state to get working directory from.
        """
        from openhands.tools.gemini.edit.impl import EditExecutor

        executor = EditExecutor(workspace_root=conv_state.workspace.working_dir)

        working_dir = conv_state.workspace.working_dir
        enhanced_description = (
            f"{TOOL_DESCRIPTION}\n\n"
            f"Your current working directory is: {working_dir}\n"
            f"File paths can be absolute or relative to this directory."
        )

        return [
            cls(
                action_type=EditAction,
                observation_type=EditObservation,
                description=enhanced_description,
                annotations=ToolAnnotations(
                    title="edit",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


register_tool(EditTool.name, EditTool)
