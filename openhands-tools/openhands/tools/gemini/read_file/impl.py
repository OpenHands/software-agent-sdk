"""Read file tool executor implementation."""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor
from openhands.tools.gemini.read_file.definition import (
    MAX_LINES_PER_READ,
    ReadFileAction,
    ReadFileObservation,
)
from openhands.tools.utils.path_containment import (
    PathContainment,
    PathOutsideWorkspaceError,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class ReadFileExecutor(ToolExecutor[ReadFileAction, ReadFileObservation]):
    """Executor for read_file tool."""

    def __init__(
        self,
        workspace_root: str,
        restrict_to_workspace: bool = False,
        allowed_roots: Sequence[str] | None = None,
    ):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
            restrict_to_workspace: Reject paths outside the workspace root
                (and ``allowed_roots``) after resolving symlinks.
            allowed_roots: Extra absolute directories allowed when restricted.
        """
        self.workspace_root = Path(workspace_root)
        self.containment: PathContainment | None = PathContainment.from_settings(
            workspace_root, restrict_to_workspace, allowed_roots
        )

    def __call__(
        self,
        action: ReadFileAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ReadFileObservation:
        """Execute read file action.

        Args:
            action: ReadFileAction with file_path, offset, and limit
            conversation: Execution context

        Returns:
            ReadFileObservation with file content
        """

        file_path = action.file_path
        offset = action.offset or 0
        limit = action.limit

        # Resolve path relative to workspace
        if not os.path.isabs(file_path):
            resolved_path = self.workspace_root / file_path
        else:
            resolved_path = Path(file_path)

        if self.containment is not None:
            try:
                self.containment.check(resolved_path)
            except PathOutsideWorkspaceError as e:
                return ReadFileObservation.from_text(
                    is_error=True,
                    text=str(e),
                    file_path=str(resolved_path),
                    file_content="",
                )

        # Check if file exists
        if not resolved_path.exists():
            return ReadFileObservation.from_text(
                text=f"Error: File not found: {resolved_path}",
                is_error=True,
                file_path=str(resolved_path),
                file_content="",
            )

        # Check if it's a directory
        if resolved_path.is_dir():
            return ReadFileObservation.from_text(
                text=f"Error: Path is a directory, not a file: {resolved_path}",
                is_error=True,
                file_path=str(resolved_path),
                file_content="",
            )

        try:
            # Read file content
            with open(resolved_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)

            # Apply offset and limit
            if offset >= total_lines:
                return ReadFileObservation.from_text(
                    text=(
                        f"Error: Offset {offset} is beyond file length "
                        f"({total_lines} lines)"
                    ),
                    is_error=True,
                    file_path=str(resolved_path),
                    file_content="",
                )

            # Determine the range to read
            start = offset
            if limit:
                end = min(start + limit, total_lines)
            else:
                # If no limit specified, apply default maximum
                end = min(start + MAX_LINES_PER_READ, total_lines)

            # Get the lines to return
            lines_to_show = lines[start:end]

            # Add line numbers
            numbered_lines = []
            for i, line in enumerate(lines_to_show, start=start + 1):
                numbered_lines.append(f"{i:6d}  {line}")
            content_with_numbers = "".join(numbered_lines)

            # Check if truncated
            is_truncated = end < total_lines
            lines_shown = (start + 1, end) if is_truncated else None

            agent_obs_parts = [f"Read file: {resolved_path}"]
            if is_truncated:
                agent_obs_parts.append(
                    f"(showing lines {start + 1}-{end} of {total_lines})"
                )
                next_offset = end
                agent_obs_parts.append(
                    f"To read more, use: read_file(file_path='{action.file_path}', "
                    f"offset={next_offset}, limit={limit or MAX_LINES_PER_READ})"
                )

            return ReadFileObservation.from_text(
                text=" ".join(agent_obs_parts) + "\n\n" + content_with_numbers,
                file_path=str(resolved_path),
                file_content=content_with_numbers,
                is_truncated=is_truncated,
                lines_shown=lines_shown,
                total_lines=total_lines,
            )

        except UnicodeDecodeError:
            return ReadFileObservation.from_text(
                is_error=True,
                text=f"Error: File is not a text file: {resolved_path}",
                file_path=str(resolved_path),
                file_content="",
            )
        except PermissionError:
            return ReadFileObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {resolved_path}",
                file_path=str(resolved_path),
                file_content="",
            )
        except Exception as e:
            return ReadFileObservation.from_text(
                is_error=True,
                text=f"Error reading file: {e}",
                file_path=str(resolved_path),
                file_content="",
            )
