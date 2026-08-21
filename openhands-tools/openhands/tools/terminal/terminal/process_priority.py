"""Platform-specific process priority policy for agent terminals."""

import platform


def get_process_priority_prefix() -> tuple[str, ...]:
    """Return an argv prefix that lowers an agent terminal's CPU priority."""
    system = platform.system()
    if system == "Darwin":
        return ("/usr/sbin/taskpolicy", "-c", "utility")
    if system == "Linux":
        return ("nice", "-n", "10")
    return ()
