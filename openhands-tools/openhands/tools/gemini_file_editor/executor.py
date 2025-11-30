"""Executors for gemini-style file editing tools."""

import os
from datetime import datetime
from pathlib import Path

from openhands.sdk.tool import ToolExecutor


class ReadFileExecutor(ToolExecutor):
    """Executor for read_file tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    async def __call__(self, action, _context=None):
        """Execute read file action.

        Args:
            action: ReadFileAction with file_path, offset, and limit
            context: Execution context

        Returns:
            ReadFileObservation with file content
        """
        from openhands.tools.gemini_file_editor.read_file import (
            MAX_LINES_PER_READ,
            ReadFileObservation,
        )

        file_path = action.file_path
        offset = action.offset or 0
        limit = action.limit

        # Resolve path relative to workspace
        if not os.path.isabs(file_path):
            file_path = self.workspace_root / file_path
        else:
            file_path = Path(file_path)

        # Check if file exists
        if not file_path.exists():
            return ReadFileObservation.from_text(
                text=f"Error: File not found: {file_path}",
                is_error=True,
                file_path=str(file_path),
                file_content="",
            )

        # Check if it's a directory
        if file_path.is_dir():
            return ReadFileObservation.from_text(
                text=f"Error: Path is a directory, not a file: {file_path}",
                is_error=True,
                file_path=str(file_path),
                file_content="",
            )

        try:
            # Read file content
            with open(file_path, encoding="utf-8", errors="replace") as f:
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
                    file_path=str(file_path),
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

            agent_obs_parts = [f"Read file: {file_path}"]
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
                file_path=str(file_path),
                file_content=content_with_numbers,
                is_truncated=is_truncated,
                lines_shown=lines_shown,
                total_lines=total_lines,
            )

        except UnicodeDecodeError:
            return ReadFileObservation.from_text(
                is_error=True,
                text=f"Error: File is not a text file: {file_path}",
                file_path=str(file_path),
                file_content="",
            )
        except PermissionError:
            return ReadFileObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {file_path}",
                file_path=str(file_path),
                file_content="",
            )
        except Exception as e:
            return ReadFileObservation.from_text(
                is_error=True,
                text=f"Error reading file: {e}",
                file_path=str(file_path),
                file_content="",
            )


class WriteFileExecutor(ToolExecutor):
    """Executor for write_file tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    async def __call__(self, action, _context=None):
        """Execute write file action.

        Args:
            action: WriteFileAction with file_path and content
            context: Execution context

        Returns:
            WriteFileObservation with result
        """
        from openhands.tools.gemini_file_editor.write_file import (
            WriteFileObservation,
        )

        file_path = action.file_path
        content = action.content

        # Resolve path relative to workspace
        if not os.path.isabs(file_path):
            file_path = self.workspace_root / file_path
        else:
            file_path = Path(file_path)

        # Check if path is a directory
        if file_path.exists() and file_path.is_dir():
            return WriteFileObservation.from_text(
                is_error=True,
                text=(f"Error: Path is a directory, not a file: {file_path}"),
            )

        # Read old content if file exists
        is_new_file = not file_path.exists()
        old_content = None
        if not is_new_file:
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
            except Exception:
                pass

        try:
            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write the file
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            action_verb = "Created" if is_new_file else "Updated"
            return WriteFileObservation.from_text(
                text=f"{action_verb} file: {file_path}",
                file_path=str(file_path),
                is_new_file=is_new_file,
                old_content=old_content,
                new_content=content,
            )

        except PermissionError:
            return WriteFileObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {file_path}",
            )
        except Exception as e:
            return WriteFileObservation.from_text(
                is_error=True,
                text=f"Error writing file: {e}",
            )


class EditExecutor(ToolExecutor):
    """Executor for edit tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    async def __call__(self, action, _context=None):
        """Execute edit action.

        Args:
            action: EditAction with file_path, old_string, new_string, etc.
            context: Execution context

        Returns:
            EditObservation with result
        """
        from openhands.tools.gemini_file_editor.edit import EditObservation

        file_path = action.file_path
        old_string = action.old_string
        new_string = action.new_string
        expected_replacements = action.expected_replacements

        # Resolve path relative to workspace
        if not os.path.isabs(file_path):
            file_path = self.workspace_root / file_path
        else:
            file_path = Path(file_path)

        # Handle file creation (old_string is empty)
        if old_string == "":
            if file_path.exists():
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        f"Error: Cannot create file that already exists: {file_path}. "
                        f"Use write_file to overwrite or provide non-empty old_string."
                    ),
                )

            try:
                # Create parent directories if needed
                file_path.parent.mkdir(parents=True, exist_ok=True)

                # Write the file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_string)

                return EditObservation.from_text(
                    text=f"Created new file: {file_path}",
                    file_path=str(file_path),
                    is_new_file=True,
                    replacements_made=1,
                    old_content=None,
                    new_content=new_string,
                )

            except PermissionError:
                return EditObservation.from_text(
                    is_error=True,
                    text=f"Error: Permission denied: {file_path}",
                )
            except Exception as e:
                return EditObservation.from_text(
                    is_error=True,
                    text=f"Error creating file: {e}",
                )

        # Editing existing file
        if not file_path.exists():
            return EditObservation.from_text(
                is_error=True,
                text=(
                    f"Error: File not found: {file_path}. "
                    f"To create a new file, use old_string=''."
                ),
            )

        if file_path.is_dir():
            return EditObservation.from_text(
                is_error=True,
                text=f"Error: Path is a directory, not a file: {file_path}",
            )

        try:
            # Read current content
            with open(file_path, encoding="utf-8", errors="replace") as f:
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
                        f"0 occurrences found in {file_path}. "
                        f"Use read_file to verify the exact text."
                    ),
                    file_path=str(file_path),
                )

            if occurrences != expected_replacements:
                occurrence_word = (
                    "occurrence" if expected_replacements == 1 else "occurrences"
                )
                return EditObservation.from_text(
                    is_error=True,
                    text=(
                        f"Error: Expected {expected_replacements} {occurrence_word} "
                        f"but found {occurrences} in {file_path}."
                    ),
                    file_path=str(file_path),
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
                    file_path=str(file_path),
                )

            # Write the file
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return EditObservation.from_text(
                text=(
                    f"Successfully edited {file_path} ({occurrences} replacement(s))"
                ),
                file_path=str(file_path),
                is_new_file=False,
                replacements_made=occurrences,
                old_content=old_content,
                new_content=new_content,
            )

        except PermissionError:
            return EditObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {file_path}",
            )
        except Exception as e:
            return EditObservation.from_text(
                is_error=True,
                text=f"Error editing file: {e}",
            )


class ListDirectoryExecutor(ToolExecutor):
    """Executor for list_directory tool."""

    def __init__(self, workspace_root: str):
        """Initialize executor with workspace root.

        Args:
            workspace_root: Root directory for file operations
        """
        self.workspace_root = Path(workspace_root)

    async def __call__(self, action, _context=None):
        """Execute list directory action.

        Args:
            action: ListDirectoryAction with dir_path and recursive
            context: Execution context

        Returns:
            ListDirectoryObservation with directory contents
        """
        from openhands.tools.gemini_file_editor.list_directory import (
            MAX_ENTRIES,
            FileEntry,
            ListDirectoryObservation,
        )

        dir_path = action.dir_path
        recursive = action.recursive

        # Resolve path relative to workspace
        if not os.path.isabs(dir_path):
            dir_path = self.workspace_root / dir_path
        else:
            dir_path = Path(dir_path)

        # Check if directory exists
        if not dir_path.exists():
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error: Directory not found: {dir_path}",
            )

        # Check if it's a directory
        if not dir_path.is_dir():
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error: Path is not a directory: {dir_path}",
            )

        try:
            entries = []

            if recursive:
                # List up to 2 levels deep
                for root, dirs, files in os.walk(dir_path):
                    root_path = Path(root)
                    depth = len(root_path.relative_to(dir_path).parts)
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
                for entry in sorted(dir_path.iterdir()):
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

            agent_obs = f"Listed directory: {dir_path} ({total_count} entries"
            if is_truncated:
                agent_obs += f", truncated to {MAX_ENTRIES}"
            agent_obs += ")"

            return ListDirectoryObservation.from_text(
                text=agent_obs,
                dir_path=str(dir_path),
                entries=entries[:MAX_ENTRIES],
                total_count=total_count,
                is_truncated=is_truncated,
            )

        except PermissionError:
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error: Permission denied: {dir_path}",
            )
        except Exception as e:
            return ListDirectoryObservation.from_text(
                is_error=True,
                text=f"Error listing directory: {e}",
            )
