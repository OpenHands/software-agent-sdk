"""Grep tool executor implementation."""

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor
from openhands.sdk.utils import sanitized_env


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
from openhands.tools.grep.definition import GrepAction, GrepObservation
from openhands.tools.utils import (
    _check_grep_available,
    _check_ripgrep_available,
    _log_ripgrep_fallback_warning,
)


logger = get_logger(__name__)

_BRACE_GROUP = re.compile(r"\{([^{}]*)\}")


def _expand_braces(pattern: str) -> list[str]:
    """Expand `{a,b}` alternatives, including nested groups, into plain globs."""
    match = _BRACE_GROUP.search(pattern)
    if match is None:
        return [pattern]
    expanded: list[str] = []
    for alternative in match.group(1).split(","):
        expanded.extend(
            _expand_braces(
                pattern[: match.start()] + alternative + pattern[match.end() :]
            )
        )
    return list(dict.fromkeys(expanded))


def _character_class_end(pattern: str, start: int) -> int | None:
    """Return the index of the `]` closing the class opened at `start`."""
    i = start + 1
    if i < len(pattern) and pattern[i] in "!^":
        i += 1
    if i < len(pattern) and pattern[i] == "]":
        i += 1
    while i < len(pattern) and pattern[i] != "]":
        i += 1
    return i if i < len(pattern) else None


def _glob_to_regex(pattern: str) -> str:
    """Translate a glob so `*` and `?` stay within one path segment.

    A `**` covering a whole path segment matches zero or more segments, which
    mirrors how both ripgrep and gitignore treat globstar patterns.
    """
    regex: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            star_start = i
            while i < len(pattern) and pattern[i] == "*":
                i += 1
            whole_segment = star_start == 0 or pattern[star_start - 1] == "/"
            if i - star_start >= 2 and whole_segment and i == len(pattern):
                regex.append(".*")
            elif i - star_start >= 2 and whole_segment and pattern[i] == "/":
                i += 1
                # `**/**/` matches the same paths as `**/`, and adjacent
                # repetitions would otherwise backtrack catastrophically.
                while pattern.startswith("**/", i):
                    i += 3
                regex.append("(?:[^/]*/)*")
            else:
                regex.append("[^/]*")
        elif char == "?":
            regex.append("[^/]")
            i += 1
        elif char == "[":
            end = _character_class_end(pattern, i)
            if end is None:
                regex.append(re.escape(char))
                i += 1
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                body = body.replace("\\", "\\\\")
                regex.append(f"[{body}]")
                i = end + 1
        else:
            regex.append(re.escape(char))
            i += 1
    return "".join(regex)


@lru_cache(maxsize=256)
def _compile_include_patterns(
    include_pattern: str,
) -> tuple[tuple[re.Pattern[str], bool], ...]:
    """Compile an include pattern into (regex, match-against-full-path) pairs.

    Like ripgrep, globs containing a `/` are matched against the path relative
    to the search root; globs without one are matched against the file name.
    """
    return tuple(
        (re.compile(_glob_to_regex(glob)), "/" in glob)
        for glob in _expand_braces(include_pattern)
    )


def _include_pattern_matches(relative_path: str, include_pattern: str) -> bool:
    filename = relative_path.rpartition("/")[2]
    for regex, match_relative_path in _compile_include_patterns(include_pattern):
        candidate = relative_path if match_relative_path else filename
        if regex.fullmatch(candidate):
            return True
    return False


class GrepExecutor(ToolExecutor[GrepAction, GrepObservation]):
    """Executor for grep content search operations.

    This implementation prefers ripgrep for performance, falls back to the
    system grep binary when available, and finally uses a Python recursive
    search when no grep binary is installed.
    """

    _MAX_MATCHES = 100

    def __init__(self, working_dir: str):
        """Initialize the grep executor.

        Args:
            working_dir: The working directory to use as the base for searches
        """
        self.working_dir: Path = Path(working_dir).resolve()
        self._search_backend = self._select_search_backend()

        if self._search_backend == "grep":
            _log_ripgrep_fallback_warning("grep", "system grep")
        elif self._search_backend == "python":
            _log_ripgrep_fallback_warning("grep", "system grep, then Python search")

    def _select_search_backend(self) -> str:
        if _check_ripgrep_available():
            return "ripgrep"
        if _check_grep_available():
            return "grep"
        return "python"

    def __call__(
        self,
        action: GrepAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> GrepObservation:
        """Execute grep content search using the best available backend."""
        try:
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

            try:
                regex = re.compile(action.pattern, re.IGNORECASE)
            except re.error as e:
                return GrepObservation.from_text(
                    text=f"Invalid regex pattern: {e}",
                    matches=[],
                    pattern=action.pattern,
                    search_path=str(search_path),
                    include_pattern=action.include,
                    is_error=True,
                )

            if self._search_backend == "ripgrep":
                return self._execute_with_ripgrep(action, search_path)
            if self._search_backend == "grep":
                return self._execute_with_system_grep(action, search_path)
            return self._execute_with_python_search(action, search_path, regex)

        except Exception as e:
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

    def _path_matches_filters(
        self,
        path: Path,
        search_path: Path,
        include_pattern: str | None,
    ) -> bool:
        """Return whether a matched path should be surfaced to the user."""
        try:
            relative_parts = path.resolve().relative_to(search_path.resolve()).parts
        except ValueError:
            relative_parts = (path.name,)

        if any(part.startswith(".") for part in relative_parts[:-1]):
            return False

        filename = relative_parts[-1] if relative_parts else path.name
        if include_pattern:
            relative_path = "/".join(relative_parts) if relative_parts else path.name
            return _include_pattern_matches(relative_path, include_pattern)
        return not filename.startswith(".")

    def _match_mtime(self, path: Path) -> float:
        """Return a sortable modification time for matched paths."""
        try:
            return path.stat().st_mtime
        except OSError:
            return float("-inf")

    def _finalize_matches(
        self,
        matches: list[Path],
        search_path: Path,
        include_pattern: str | None,
    ) -> tuple[list[str], bool]:
        """Filter, sort, and truncate raw match paths."""
        unique_matches: dict[str, Path] = {}
        for match in matches:
            try:
                resolved = match.resolve()
            except OSError:
                continue
            if not self._path_matches_filters(resolved, search_path, include_pattern):
                continue
            unique_matches[str(resolved)] = resolved

        sorted_matches = sorted(
            unique_matches.values(),
            key=self._match_mtime,
            reverse=True,
        )
        truncated = len(sorted_matches) > self._MAX_MATCHES
        return [str(path) for path in sorted_matches[: self._MAX_MATCHES]], truncated

    def _build_observation(
        self,
        action: GrepAction,
        search_path: Path,
        matches: list[Path],
    ) -> GrepObservation:
        formatted_matches, truncated = self._finalize_matches(
            matches,
            search_path,
            action.include,
        )
        output = self._format_output(
            matches=formatted_matches,
            pattern=action.pattern,
            search_path=str(search_path),
            include_pattern=action.include,
            truncated=truncated,
        )
        return GrepObservation.from_text(
            text=output,
            matches=formatted_matches,
            pattern=action.pattern,
            search_path=str(search_path),
            include_pattern=action.include,
            truncated=truncated,
        )

    def _execute_with_ripgrep(
        self, action: GrepAction, search_path: Path
    ) -> GrepObservation:
        """Execute grep content search using ripgrep."""
        cmd = [
            "rg",
            "-l",
            "-i",
            action.pattern,
            str(search_path),
            "--sortr=modified",
        ]
        if action.include:
            cmd.extend(["-g", action.include])

        # ripgrep resolves path-bearing globs against its working directory,
        # so run it from the search root to keep include patterns relative to it.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=sanitized_env(),
            cwd=search_path,
        )

        matches = []
        if result.stdout:
            matches = [Path(line) for line in result.stdout.splitlines() if line]

        return self._build_observation(action, search_path, matches)

    def _execute_with_system_grep(
        self, action: GrepAction, search_path: Path
    ) -> GrepObservation:
        """Execute grep content search using the system grep binary."""
        result = subprocess.run(
            ["grep", "-R", "-I", "-l", "-i", action.pattern, str(search_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=sanitized_env(),
        )
        if result.returncode not in (0, 1):
            logger.warning(
                "grep backend failed with exit code %s; falling back to Python search",
                result.returncode,
            )
            return self._execute_with_python_search(action, search_path)

        matches = []
        if result.stdout:
            matches = [Path(line) for line in result.stdout.splitlines() if line]

        return self._build_observation(action, search_path, matches)

    def _execute_with_python_search(
        self,
        action: GrepAction,
        search_path: Path,
        regex: re.Pattern[str] | None = None,
    ) -> GrepObservation:
        """Execute grep content search using Python file walking."""
        compiled_regex = regex or re.compile(action.pattern, re.IGNORECASE)
        matches: list[Path] = []
        for root, dirs, files in os.walk(search_path):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for filename in files:
                file_path = Path(root) / filename
                if not self._path_matches_filters(
                    file_path, search_path, action.include
                ):
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if compiled_regex.search(content):
                    matches.append(file_path)

        return self._build_observation(action, search_path, matches)
