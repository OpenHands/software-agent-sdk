"""String replace executor implementation (Nemotron/Anthropic-compatible).

This executor wraps FileEditorExecutor to provide Anthropic-compatible
str_replace tool functionality.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor
from openhands.tools.file_editor.editor import FileEditor
from openhands.tools.file_editor.exceptions import ToolError
from openhands.tools.nemotron.str_replace.definition import (
    StrReplaceAction,
    StrReplaceObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class StrReplaceExecutor(ToolExecutor[StrReplaceAction, StrReplaceObservation]):
    """String replace executor that wraps FileEditor for Anthropic compatibility."""

    def __init__(
        self,
        workspace_root: str | None = None,
        allowed_edits_files: list[str] | None = None,
    ):
        self.editor: FileEditor = FileEditor(workspace_root=workspace_root)
        self.allowed_edits_files: set[Path] | None = (
            {Path(f).resolve() for f in allowed_edits_files}
            if allowed_edits_files
            else None
        )

    def __call__(
        self,
        action: StrReplaceAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> StrReplaceObservation:
        # Enforce allowed_edits_files restrictions
        if self.allowed_edits_files is not None and action.command != "view":
            action_path = Path(action.path).resolve()
            if action_path not in self.allowed_edits_files:
                return StrReplaceObservation.from_text(
                    text=(
                        f"Operation '{action.command}' is not allowed "
                        f"on file '{action_path}'. "
                        f"Only the following files can be edited: "
                        f"{sorted(str(p) for p in self.allowed_edits_files)}"
                    ),
                    command=action.command,
                    is_error=True,
                )

        result: StrReplaceObservation | None = None
        try:
            # Call the FileEditor and convert the result
            file_editor_result = self.editor(
                command=action.command,
                path=action.path,
                file_text=action.file_text,
                view_range=action.view_range,
                old_str=action.old_str,
                new_str=action.new_str,
                insert_line=action.insert_line,
            )
            # Convert FileEditorObservation to StrReplaceObservation
            result = StrReplaceObservation(
                content=file_editor_result.content,
                is_error=file_editor_result.is_error,
                command=file_editor_result.command,
                path=file_editor_result.path,
                prev_exist=file_editor_result.prev_exist,
                old_content=file_editor_result.old_content,
                new_content=file_editor_result.new_content,
            )
        except ToolError as e:
            result = StrReplaceObservation.from_text(
                text=e.message, command=action.command, is_error=True
            )
        assert result is not None, "str_replace should always return a result"
        return result
