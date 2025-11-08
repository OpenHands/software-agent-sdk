"""Shared utilities."""

import shutil
import subprocess

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def _check_ripgrep_available() -> bool:
    """Check if ripgrep (rg) is available on the system.

    Returns:
        True if ripgrep is available, False otherwise
    """

    try:
        # First check if rg is in PATH
        if shutil.which("rg") is None:
            return False

        # Try to run rg --version to ensure it's working
        result = subprocess.run(
            ["rg", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
        return result.returncode == 0
    except Exception:
        return False


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
