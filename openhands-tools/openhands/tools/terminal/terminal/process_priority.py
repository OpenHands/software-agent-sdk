"""Lower the CPU priority of agent terminals so they cannot starve the server."""

import os
import platform
import shutil
from collections.abc import Mapping
from typing import Final


TERMINAL_PROCESS_PRIORITY_ENV: Final[str] = "OH_TERMINAL_PROCESS_PRIORITY"

# macOS largely ignores nice; the utility QoS clamp is what keeps the server
# responsive. Avoid the background clamp: it also throttles I/O heavily.
_TASKPOLICY_PATH: Final[str] = "/usr/sbin/taskpolicy"
_LINUX_NICE_INCREMENT: Final[str] = "10"


def should_lower_process_priority(env: Mapping[str, str]) -> bool:
    """Return False when the terminal env opts out with ``none``."""
    value = env.get(TERMINAL_PROCESS_PRIORITY_ENV, "")
    return value.strip().lower() != "none"


def process_priority_prefix(env: Mapping[str, str]) -> list[str]:
    """Return an argv prefix that runs the shell at lower CPU priority.

    This must be an argv prefix rather than a ``preexec_fn``: a macOS QoS class
    set on the calling thread does not survive ``exec``. Both ``taskpolicy``
    and ``nice`` are inherited by every process the shell starts.
    """
    if not should_lower_process_priority(env):
        return []

    system = platform.system()
    if system == "Darwin" and os.path.exists(_TASKPOLICY_PATH):
        return [_TASKPOLICY_PATH, "-c", "utility"]
    if system == "Linux" and (nice_path := shutil.which("nice")) is not None:
        return [nice_path, "-n", _LINUX_NICE_INCREMENT]
    return []
