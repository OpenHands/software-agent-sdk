"""Grep tool executor implementation."""

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
from openhands.tools.grep.definition import GrepAction, GrepObservation
from openhands.tools.utils import (
    _check_ripgrep_available,
    _log_ripgrep_fallback_warning,
)


class GrepExecutor(ToolExecutor[GrepAction, GrepObservation]):
    """Executor for grep content search operations.

    This implementation prefers ripgrep for performance but falls back to
    regular grep if ripgrep is not available:
    - Primary: Uses ripgrep with case-insensitive search and file listing
    - Fallback: Uses regular grep command with similar functionality
    """

    def __init__(self, working_dir: str):
        """Initialize the grep executor.

        Args:
            working_dir: The working directory to use as the base for searches
        """
        self.working_dir: Path = Path(working_dir).resolve()
        self._ripgrep_available: bool = _check_ripgrep_available()
        if not self._ripgrep_available:
            _log_ripgrep_fallback_warning("grep", "regular grep command")

    def __call__(
        self,
        action: GrepAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> GrepObservation:
        """Execute grep content search using ripgrep or fallback to regular grep.

        Args:
            action: The grep action containing pattern, path, and include filter

        Returns:
            GrepObservation with matching file paths
        """
        try:
            # Determine search path
            if action.path:
                search_path = Path(action.path).resolve()
                if not search_path.is_dir():
                    return GrepObservation.from_text(
                        text=f"Search path '{action.path}' is not a valid directory",
                        matches=[],
                        pattern=action.pattern,
                        search_path=str(search_path),
                        include_pattern=action.include,
                        is_error=True,
                    )
            else:
                search_path = self.working_dir

            # Validate regex pattern
            try:
                re.compile(action.pattern)
            except re.error as e:
                return GrepObservation.from_text(
                    text=f"Invalid regex pattern: {e}",
                    matches=[],
                    pattern=action.pattern,
                    search_path=str(search_path),
                    include_pattern=action.include,
                    is_error=True,
                )

            if self._ripgrep_available:
                return self._execute_with_ripgrep(action, search_path)
            else:
                return self._execute_with_grep(action, search_path)

        except Exception as e:
            # Determine search path for error reporting
            try:
                if action.path:
                    error_search_path = str(Path(action.path).resolve())
                else:
                    error_search_path = str(self.working_dir)
            except Exception:
                error_search_path = "unknown"

            return GrepObservation.from_text(
                text=str(e),
                matches=[],
                pattern=action.pattern,
                search_path=error_search_path,
                include_pattern=action.include,
                is_error=True,
            )

    def _format_output(
        self,
        matches: list[str],
        pattern: str,
        search_path: str,
        include_pattern: str | None,
        truncated: bool,
    ) -> str:
        """Format the grep observation output message."""
        if not matches:
            include_info = (
                f" (filtered by '{include_pattern}')" if include_pattern else ""
            )
            return (
                f"No files found containing pattern '{pattern}' "
                f"in directory '{search_path}'{include_info}"
            )

        include_info = f" (filtered by '{include_pattern}')" if include_pattern else ""
        file_list = "\n".join(matches)
        output = (
            f"Found {len(matches)} file(s) containing pattern "
            f"'{pattern}' in '{search_path}'{include_info}:\n{file_list}"
        )
        if truncated:
            output += (
                "\n\n[Results truncated to first 100 files. "
                "Consider using a more specific pattern.]"
            )
        return output

    def _execute_with_ripgrep(
        self, action: GrepAction, search_path: Path
    ) -> GrepObservation:
        """Execute grep content search using ripgrep."""
        # Build ripgrep command: rg -li pattern --sortr=modified
        cmd = [
            "rg",
            "-l",  # files-with-matches
            "-i",  # ignore-case
            action.pattern,
            str(search_path),
            "--sortr=modified",
        ]

        # Apply include glob pattern if specified
        if action.include:
            cmd.extend(["-g", action.include])

        # Execute ripgrep
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )

        # Parse output into file paths
        matches = []
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line:
                    matches.append(line)
                    # Limit to first 100 files
                    if len(matches) >= 100:
                        break

        truncated = len(matches) >= 100

        output = self._format_output(
            matches=matches,
            pattern=action.pattern,
            search_path=str(search_path),
            include_pattern=action.include,
            truncated=truncated,
        )

        return GrepObservation.from_text(
            text=output,
            matches=matches,
            pattern=action.pattern,
            search_path=str(search_path),
            include_pattern=action.include,
            truncated=truncated,
        )

    def _execute_with_grep(
        self, action: GrepAction, search_path: Path
    ) -> GrepObservation:
        """Execute grep content search using regular grep command."""
        # Build grep command: grep -r -l -I -i pattern path
        cmd = [
            "grep",
            "-r",  # recursive
            "-l",  # files-with-matches
            "-I",  # ignore binary files
            "-i",  # ignore-case
            action.pattern,
            str(search_path),
            "--exclude-dir=.*",  # exclude hidden directories to match ripgrep behavior
            "--exclude=.*",  # exclude hidden files to match ripgrep behavior
        ]

        # Add include pattern using --include if specified
        if action.include:
            cmd.extend(["--include", action.include])

        # Execute grep
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )

        # Parse output into file paths
        matches = []
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line:
                    # Apply include pattern filtering if --include didn't work properly
                    # This ensures consistent behavior across different grep versions
                    if action.include:
                        import fnmatch

                        filename = Path(line).name
                        if not fnmatch.fnmatch(filename, action.include):
                            continue

                    matches.append(line)
                    if len(matches) >= 100:
                        break

        truncated = len(matches) >= 100

        output = self._format_output(
            matches=matches,
            pattern=action.pattern,
            search_path=str(search_path),
            include_pattern=action.include,
            truncated=truncated,
        )

        return GrepObservation.from_text(
            text=output,
            matches=matches,
            pattern=action.pattern,
            search_path=str(search_path),
            include_pattern=action.include,
            truncated=truncated,
        )
