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
