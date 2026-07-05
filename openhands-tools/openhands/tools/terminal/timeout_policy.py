"""Runtime-idle-aware terminal timeout policy."""

import os
from collections.abc import Mapping


RUNTIME_IDLE_TIMEOUT_SECONDS_ENV = "OH_RUNTIME_IDLE_TIMEOUT_SECONDS"
MAX_FOREGROUND_TIMEOUT_RATIO = 0.9


def get_runtime_idle_timeout_seconds(
    env: Mapping[str, str] | None = None,
) -> float | None:
    """Return configured runtime idle timeout in seconds, if any.

    The runtime-api injects this value into managed runtime pods. Local and
    self-hosted deployments that do not have idle cleanup can leave it unset,
    which disables the foreground terminal timeout cap.
    """
    raw = (env or os.environ).get(RUNTIME_IDLE_TIMEOUT_SECONDS_ENV)
    if raw is None or raw.strip() == "":
        return None

    try:
        value = float(raw)
    except ValueError:
        return None

    if value <= 0:
        return None
    return value


def get_max_foreground_timeout_seconds(
    runtime_idle_timeout_seconds: float | None = None,
) -> float | None:
    """Return max safe foreground terminal timeout, or None when uncapped."""
    idle_timeout = (
        get_runtime_idle_timeout_seconds()
        if runtime_idle_timeout_seconds is None
        else runtime_idle_timeout_seconds
    )
    if idle_timeout is None:
        return None
    return idle_timeout * MAX_FOREGROUND_TIMEOUT_RATIO


def format_seconds(value: float) -> str:
    """Format second values compactly for user-facing observations."""
    return f"{value:g}"


def foreground_timeout_rejection_message(
    requested_timeout_seconds: float,
    runtime_idle_timeout_seconds: float,
) -> str:
    """Build the user-facing rejection for unsafe foreground timeouts."""
    max_timeout = get_max_foreground_timeout_seconds(runtime_idle_timeout_seconds)
    assert max_timeout is not None
    ratio_percent = int(MAX_FOREGROUND_TIMEOUT_RATIO * 100)
    return (
        "Refusing to run foreground terminal command with "
        f"timeout={format_seconds(requested_timeout_seconds)}s.\n\n"
        "This runtime is subject to idle cleanup after "
        f"{format_seconds(runtime_idle_timeout_seconds)}s. Foreground terminal "
        f"commands are capped at {ratio_percent}% of that threshold, currently "
        f"{format_seconds(max_timeout)}s, so commands can return control before "
        "the runtime is considered idle.\n\n"
        "For long-running work, use a shorter timeout, omit the timeout to use "
        "the terminal soft-timeout/polling flow, or start the command in the "
        "background and poll its log file."
    )


def foreground_timeout_rejection_for(
    *,
    command: str,
    is_input: bool,
    timeout: float | None,
    runtime_idle_timeout_seconds: float | None = None,
) -> str | None:
    """Return a rejection message if a terminal action is unsafe to run."""
    if is_input or not command.strip() or timeout is None:
        return None

    idle_timeout = (
        get_runtime_idle_timeout_seconds()
        if runtime_idle_timeout_seconds is None
        else runtime_idle_timeout_seconds
    )
    max_timeout = get_max_foreground_timeout_seconds(idle_timeout)
    if idle_timeout is None or max_timeout is None or timeout <= max_timeout:
        return None

    return foreground_timeout_rejection_message(timeout, idle_timeout)
