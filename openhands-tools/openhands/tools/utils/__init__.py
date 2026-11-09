"""Shared utilities."""

import shutil
import subprocess
from collections.abc import Sequence

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def _check_command_available(
    command: str,
    probe_args: Sequence[str] | None = ("--version",),
) -> bool:
    """Check if a command is available and optionally responds to a probe."""

    try:
        if shutil.which(command) is None:
            return False
        if probe_args is None:
            return True
        result = subprocess.run(
            [command, *probe_args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_ripgrep_available() -> bool:
    """Check if ripgrep (rg) is available on the system."""

    return _check_command_available("rg")


def _check_grep_available() -> bool:
    """Check if grep is available on the system."""

    return _check_command_available("grep", probe_args=None)


def _log_ripgrep_fallback_warning(tool_name: str, fallback_method: str) -> None:
    """Log a warning about falling back from ripgrep to alternative method.

    Args:
        tool_name: Name of the tool (e.g., "glob", "grep")
        fallback_method: Description of the fallback method being used
    """
    logger.warning(
        f"{tool_name}: ripgrep (rg) not available. "
        f"Falling back to {fallback_method}. "
        f"For better performance, consider installing ripgrep: "
        f"https://github.com/BurntSushi/ripgrep#installation"
    )


def expand_braces(pattern: str) -> list[str]:
    """Expand ``{a,b}`` groups, which ripgrep supports and fnmatch and glob do not."""
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    # Split the first group on its top-level commas; nested groups are
    # expanded by the recursion.
    depth, options, option_start = 0, [], start + 1
    for end in range(start, len(pattern)):
        if pattern[end] == "{":
            depth += 1
        elif pattern[end] == "}":
            depth -= 1
            if depth == 0:
                break
        elif pattern[end] == "," and depth == 1:
            options.append(pattern[option_start:end])
            option_start = end + 1
    else:
        return [pattern]  # unbalanced: leave it to the caller as-is
    options.append(pattern[option_start:end])
    head, tail = pattern[:start], pattern[end + 1 :]
    return [
        expanded
        for option in options
        for expanded in expand_braces(head + option + tail)
    ]
