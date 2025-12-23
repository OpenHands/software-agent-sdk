"""Edit tool executor implementation."""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor
from openhands.tools.gemini.edit.definition import EditAction, EditObservation


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class EditExecutor(ToolExecutor[EditAction, EditObservation]):
    """Executor for edit tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    def __call__(
        self,
        action: EditAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> EditObservation:
        """Execute edit action.

        Args:
            action: EditAction with file_path, old_string, new_string, etc.
            conversation: Execution context

        Returns:
            EditObservation with result
        """

        file_path = action.file_path
        old_string = action.old_string
        new_string = action.new_string
        expected_replacements = action.expected_replacements

        # Resolve path relative to workspace
        if not os.path.isabs(file_path):
            resolved_path = self.workspace_root / file_path
        else:
            resolved_path = Path(file_path)

        # Handle file creation (old_string is empty)
        if old_string == "":
            if resolved_path.exists():
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        f"Error: Cannot create file that already exists: "
                        f"{resolved_path}. "
                        f"Use write_file to overwrite or provide non-empty old_string."
                    ),
                )

            try:
                # Create parent directories if needed
                resolved_path.parent.mkdir(parents=True, exist_ok=True)

                # Write the file
                with open(resolved_path, "w", encoding="utf-8") as f:
                    f.write(new_string)

                return EditObservation.from_text(
                    text=f"Created new file: {resolved_path}",
                    file_path=str(resolved_path),
                    is_new_file=True,
                    replacements_made=1,
                    old_content=None,
                    new_content=new_string,
                )

            except PermissionError:
                return EditObservation.from_text(
                    is_error=True,
                    text=f"Error: Permission denied: {resolved_path}",
                )
            except Exception as e:
                return EditObservation.from_text(
                    is_error=True,
                    text=f"Error creating file: {e}",
                )

        # Editing existing file
        if not resolved_path.exists():
            return EditObservation.from_text(
                is_error=True,
                text=(
                    f"Error: File not found: {resolved_path}. "
                    f"To create a new file, use old_string=''."
                ),
            )

        if resolved_path.is_dir():
            return EditObservation.from_text(
                is_error=True,
                text=f"Error: Path is a directory, not a file: {resolved_path}",
            )

        try:
            # Read current content
            with open(resolved_path, encoding="utf-8", errors="replace") as f:
                old_content = f.read()

            # Check for no-op
            if old_string == new_string:
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        "Error: No changes to apply. "
                        "old_string and new_string are identical."
                    ),
                )

            # Count occurrences
            occurrences = old_content.count(old_string)

            if occurrences == 0:
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        f"Error: Could not find the string to replace. "
                        f"0 occurrences found in {resolved_path}. "
                        f"Use read_file to verify the exact text."
                    ),
                    file_path=str(resolved_path),
                )

            if occurrences != expected_replacements:
                occurrence_word = (
                    "occurrence" if expected_replacements == 1 else "occurrences"
                )
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        f"Error: Expected {expected_replacements} {occurrence_word} "
                        f"but found {occurrences} in {resolved_path}."
                    ),
                    file_path=str(resolved_path),
                )

            # Perform replacement
            new_content = old_content.replace(old_string, new_string)

            # Check if content actually changed
            if old_content == new_content:
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        "Error: No changes made. "
                        "The new content is identical to the current content."
                    ),
                    file_path=str(resolved_path),
                )

            # Write the file
            with open(resolved_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            msg = f"Successfully edited {resolved_path} ({occurrences} replacement(s))"
            return EditObservation.from_text(
                text=msg,
                file_path=str(resolved_path),
                is_new_file=False,
                replacements_made=occurrences,
                old_content=old_content,
                new_content=new_content,
            )

        except PermissionError:
            return EditObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {resolved_path}",
            )
        except Exception as e:
            return EditObservation.from_text(
                is_error=True,
                text=f"Error editing file: {e}",
            )
