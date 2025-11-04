"""Glob tool executor implementation."""

# Use absolute import to avoid conflict with our local glob module
import glob as glob_module
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
from openhands.tools.glob.definition import GlobAction, GlobObservation
from openhands.tools.utils import (
    _check_ripgrep_available,
    _log_ripgrep_fallback_warning,
)


class GlobExecutor(ToolExecutor[GlobAction, GlobObservation]):
    """Executor for glob pattern matching operations.

    This implementation prefers ripgrep for performance but falls back to
    Python's glob module if ripgrep is not available:
    - Primary: Uses rg --files to list all files, filters by glob pattern with -g flag
    - Fallback: Uses Python's glob.glob() for pattern matching
    """

    def __init__(self, working_dir: str):
        """Initialize the glob executor.

        Args:
            working_dir: The working directory to use as the base for searches
        """
        self.working_dir: Path = Path(working_dir).resolve()
        self._ripgrep_available: bool = _check_ripgrep_available()
        if not self._ripgrep_available:
            _log_ripgrep_fallback_warning("glob", "Python glob module")

    def __call__(
        self,
        action: GlobAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> GlobObservation:
        """Execute glob pattern matching using ripgrep or fallback to Python glob.

        Args:
            action: The glob action containing pattern and optional path

        Returns:
            GlobObservation with matching files or error information
        """
        try:
            original_pattern = action.pattern  # Store original pattern for observation

            if action.path:
                search_path = Path(action.path).resolve()
                pattern = action.pattern
            else:
                extracted_path, pattern = self._extract_search_path_from_pattern(
                    action.pattern
                )
                search_path = (
                    extracted_path if extracted_path is not None else self.working_dir
                )

            if not search_path.is_dir():
                return GlobObservation.from_text(
                    text=f"Search path '{search_path}' is not a valid directory",
                    files=[],
                    pattern=original_pattern,
                    search_path=str(search_path),
                    is_error=True,
                )

            if self._ripgrep_available:
                files, truncated = self._execute_with_ripgrep(pattern, search_path)
            else:
                files, truncated = self._execute_with_glob(pattern, search_path)

            # Format content message
            if not files:
                content = (
                    f"No files found matching pattern '{original_pattern}' "
                    f"in directory '{search_path}'"
                )
            else:
                file_list = "\n".join(files)
                content = (
                    f"Found {len(files)} file(s) matching pattern "
                    f"'{original_pattern}' in '{search_path}':\n{file_list}"
                )
                if truncated:
                    content += (
                        "\n\n[Results truncated to first 100 files. "
                        "Consider using a more specific pattern.]"
                    )

            return GlobObservation.from_text(
                text=content,
                files=files,
                pattern=original_pattern,
                search_path=str(search_path),
                truncated=truncated,
            )

        except Exception as e:
            # Determine search path for error reporting
            try:
                if action.path:
                    error_search_path = str(Path(action.path).resolve())
                else:
                    error_search_path = str(self.working_dir)
            except Exception:
                error_search_path = "unknown"

            return GlobObservation.from_text(
                text=str(e),
                files=[],
                pattern=action.pattern,
                search_path=error_search_path,
                is_error=True,
            )

    def _execute_with_ripgrep(
        self, pattern: str, search_path: Path
    ) -> tuple[list[str], bool]:
        """Execute glob pattern matching using ripgrep.

        Args:
            pattern: The glob pattern to match
            search_path: The directory to search in

        Returns:
            Tuple of (file_paths, truncated) where file_paths is a list of matching files
            and truncated is True if results were limited to 100 files
        """  # noqa: E501
        # Build ripgrep command: rg --files {path} -g {pattern} --sortr=modified
        cmd = [
            "rg",
            "--files",
            str(search_path),
            "-g",
            pattern,
            "--sortr=modified",
        ]

        # Execute ripgrep
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )

        # Parse output into file paths
        file_paths = []
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line:
                    file_paths.append(line)
                    # Limit to first 100 files
                    if len(file_paths) >= 100:
                        break

        truncated = len(file_paths) >= 100

        return file_paths, truncated

    def _execute_with_glob(
        self, pattern: str, search_path: Path
    ) -> tuple[list[str], bool]:
        """Execute glob pattern matching using Python's glob module.

        Args:
            pattern: The glob pattern to match
            search_path: The directory to search in

        Returns:
            Tuple of (file_paths, truncated) where file_paths is a list of matching files
            and truncated is True if results were limited to 100 files
        """  # noqa: E501
        # Change to search directory for glob to work correctly
        original_cwd = os.getcwd()
        try:
            os.chdir(search_path)

            # Ripgrep's -g flag is always recursive, so we need to make the pattern
            # recursive if it doesn't already contain **
            if "**" not in pattern:
                # Convert non-recursive patterns like "*.py" to "**/*.py"
                # to match ripgrep's recursive behavior
                pattern = f"**/{pattern}"

            # Use glob to find matching files
            matches = glob_module.glob(pattern, recursive=True)

            # Convert to absolute paths (without resolving symlinks)
            # and sort by modification time
            file_paths = []
            for match in matches:
                # Use absolute() instead of resolve() to avoid resolving symlinks
                abs_path = str((search_path / match).absolute())
                if os.path.isfile(abs_path):
                    file_paths.append((abs_path, os.path.getmtime(abs_path)))

            # Sort by modification time (newest first) and extract paths
            file_paths.sort(key=lambda x: x[1], reverse=True)
            sorted_files = [path for path, _ in file_paths[:100]]

            truncated = len(file_paths) > 100

            return sorted_files, truncated
        finally:
            os.chdir(original_cwd)

    @staticmethod
    def _extract_search_path_from_pattern(pattern: str) -> tuple[Path | None, str]:
        """Extract search path and relative pattern from an absolute path pattern.

        This is needed because agents may send absolute path patterns like
        "/path/to/dir/**/*.py", but ripgrep's -g flag expects a search directory
        and a relative pattern separately. This function splits the absolute pattern
        into these two components.

        For relative patterns, returns (None, pattern) to indicate the caller should
        use its default working directory.

        Args:
            pattern: The glob pattern (may be absolute or relative)

        Returns:
            Tuple of (search_path, adjusted_pattern) where:
            - search_path: The directory to search in (None for relative patterns)
            - adjusted_pattern: The pattern relative to search_path

        Examples:
            >>> _extract_search_path_from_pattern("/path/to/dir/**/*.py")
            (Path("/path/to/dir"), "**/*.py")

            >>> _extract_search_path_from_pattern("/path/to/*.py")
            (Path("/path/to"), "*.py")

            >>> _extract_search_path_from_pattern("**/*.py")
            (None, "**/*.py")
        """
        if not pattern:
            return None, "**/*"

        # Expand ~ for user home directory
        pattern = os.path.expanduser(pattern)

        # Check if pattern is an absolute path
        if not pattern.startswith("/"):
            # Relative pattern - caller should use default working directory
            return None, pattern

        # Absolute path pattern - extract the base path
        path_obj = Path(pattern)
        parts = path_obj.parts

        # Find where the glob characters start using glob.has_magic()
        search_parts = []
        for part in parts:
            if glob_module.has_magic(part):
                break
            search_parts.append(part)

        if not search_parts:
            # Pattern starts with glob at root (e.g., "/*/*.py")
            search_path = Path("/")
            adjusted_pattern = pattern.lstrip("/")
        else:
            search_path = Path(*search_parts)
            # Get the remaining parts as the pattern
            remaining = parts[len(search_parts) :]
            adjusted_pattern = str(Path(*remaining)) if remaining else "**/*"

        return search_path.resolve(), adjusted_pattern
