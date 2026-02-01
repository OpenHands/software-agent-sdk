"""Write file tool executor implementation."""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor
from openhands.tools.gemini.write_file.definition import (
    WriteFileAction,
    WriteFileObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class WriteFileExecutor(ToolExecutor[WriteFileAction, WriteFileObservation]):
    """Executor for write_file tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    def __call__(
        self,
        action: WriteFileAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> WriteFileObservation:
        """Execute write file action.

        Args:
            action: WriteFileAction with file_path and content
            conversation: Execution context

        Returns:
            WriteFileObservation with result
        """

        file_path = action.file_path
        content = action.content

        # Resolve path relative to workspace
        if not os.path.isabs(file_path):
            resolved_path = self.workspace_root / file_path
        else:
            resolved_path = Path(file_path)

        # Check if path is a directory
        if resolved_path.exists() and resolved_path.is_dir():
            return WriteFileObservation.from_text(
                is_error=True,
                text=(f"Error: Path is a directory, not a file: {resolved_path}"),
            )

        # Read old content if file exists
        is_new_file = not resolved_path.exists()
        old_content = None
        if not is_new_file:
            try:
                with open(resolved_path, encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
            except Exception:
                pass

        try:
            # Create parent directories if needed
            resolved_path.parent.mkdir(parents=True, exist_ok=True)

            # Write the file
            with open(resolved_path, "w", encoding="utf-8") as f:
                f.write(content)

            action_verb = "Created" if is_new_file else "Updated"
            return WriteFileObservation.from_text(
                text=f"{action_verb} file: {resolved_path}",
                file_path=str(resolved_path),
                is_new_file=is_new_file,
                old_content=old_content,
                new_content=content,
            )

        except PermissionError:
            return WriteFileObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {resolved_path}",
            )
        except Exception as e:
            return WriteFileObservation.from_text(
                is_error=True,
                text=f"Error writing file: {e}",
            )
