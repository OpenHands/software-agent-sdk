"""List directory tool executor implementation."""

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor
from openhands.tools.gemini.list_directory.definition import (
    MAX_ENTRIES,
    FileEntry,
    ListDirectoryAction,
    ListDirectoryObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class ListDirectoryExecutor(
    ToolExecutor[ListDirectoryAction, ListDirectoryObservation]
):
    """Executor for list_directory tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    def __call__(
        self,
        action: ListDirectoryAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ListDirectoryObservation:
        """Execute list directory action.

        Args:
            action: ListDirectoryAction with dir_path and recursive
            conversation: Execution context

        Returns:
            ListDirectoryObservation with directory contents
        """

        dir_path = action.dir_path
        recursive = action.recursive

        # Resolve path relative to workspace
        if not os.path.isabs(dir_path):
            resolved_path = self.workspace_root / dir_path
        else:
            resolved_path = Path(dir_path)

        # Check if directory exists
        if not resolved_path.exists():
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error: Directory not found: {resolved_path}",
            )

        # Check if it's a directory
        if not resolved_path.is_dir():
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error: Path is not a directory: {resolved_path}",
            )

        try:
            entries = []

            if recursive:
                # List up to 2 levels deep
                for root, dirs, files in os.walk(resolved_path):
                    root_path = Path(root)
                    depth = len(root_path.relative_to(resolved_path).parts)
                    if depth >= 2:
                        dirs.clear()
                        continue

                    # Add directories
                    for d in sorted(dirs):
                        d_path = root_path / d
                        try:
                            stat = d_path.stat()
                            entries.append(
                                FileEntry(
                                    name=d,
                                    path=str(d_path),
                                    is_directory=True,
                                    size=0,
                                    modified_time=datetime.fromtimestamp(stat.st_mtime),
                                )
                            )
                        except Exception:
                            continue

                    # Add files
                    for f in sorted(files):
                        f_path = root_path / f
                        try:
                            stat = f_path.stat()
                            entries.append(
                                FileEntry(
                                    name=f,
                                    path=str(f_path),
                                    is_directory=False,
                                    size=stat.st_size,
                                    modified_time=datetime.fromtimestamp(stat.st_mtime),
                                )
                            )
                        except Exception:
                            continue

                    if len(entries) >= MAX_ENTRIES:
                        break
            else:
                # List only immediate contents
                for entry in sorted(resolved_path.iterdir()):
                    try:
                        stat = entry.stat()
                        entries.append(
                            FileEntry(
                                name=entry.name,
                                path=str(entry),
                                is_directory=entry.is_dir(),
                                size=0 if entry.is_dir() else stat.st_size,
                                modified_time=datetime.fromtimestamp(stat.st_mtime),
                            )
                        )

                        if len(entries) >= MAX_ENTRIES:
                            break
                    except Exception:
                        continue

            total_count = len(entries)
            is_truncated = total_count >= MAX_ENTRIES

            agent_obs = f"Listed directory: {resolved_path} ({total_count} entries"
            if is_truncated:
                agent_obs += f", truncated to {MAX_ENTRIES}"
            agent_obs += ")"

            return ListDirectoryObservation.from_text(
                text=agent_obs,
                dir_path=str(resolved_path),
                entries=entries[:MAX_ENTRIES],
                total_count=total_count,
                is_truncated=is_truncated,
            )

        except PermissionError:
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {resolved_path}",
            )
        except Exception as e:
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error listing directory: {e}",
            )
