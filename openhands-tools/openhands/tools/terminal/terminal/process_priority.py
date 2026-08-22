"""Platform-specific process priority policy for agent terminals."""

import os
import platform
import shutil
from collections.abc import Mapping


TERMINAL_PROCESS_PRIORITY_ENV = "OH_TERMINAL_PROCESS_PRIORITY"


def should_lower_process_priority(env: Mapping[str, str] | None = None) -> bool:
    """Return whether agent terminal processes should run at lower priority."""
    source = os.environ if env is None else env
    value = source.get(TERMINAL_PROCESS_PRIORITY_ENV)
    return value is None or value.strip().lower() != "none"


def get_process_priority_prefix(
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return an argv prefix that lowers an agent terminal's CPU priority."""
    if not should_lower_process_priority(env):
        return ()

    system = platform.system()
    if system == "Darwin":
        return ("/usr/sbin/taskpolicy", "-c", "utility")
    if system == "Linux":
        nice_path = shutil.which("nice")
        if nice_path is not None:
            return (os.path.abspath(nice_path), "-n", "10")
    return ()
