"""Helpers for running shells inside macOS' Seatbelt (`sandbox-exec`) sandbox.

Seatbelt is the user-facing name for the macOS application sandbox. It is
configured through a TinyScheme profile and applied to a child process via
``/usr/bin/sandbox-exec``. This module centralises the small surface we need:

* a reasonable default profile that allows agent-style work in a workspace
  while restricting writes elsewhere on disk, and
* the helper used by terminal backends to wrap a shell command in
  ``sandbox-exec -p <profile> ...``.

The agent-server is responsible for validating availability up-front (macOS +
``sandbox-exec`` on ``PATH``); this module is intentionally side-effect free.
"""

from __future__ import annotations

import platform
import shutil


SANDBOX_EXEC_BIN = "/usr/bin/sandbox-exec"


def is_seatbelt_supported() -> bool:
    """Return True if Seatbelt (`sandbox-exec`) can be used on this host."""
    if platform.system() != "Darwin":
        return False
    return shutil.which("sandbox-exec") is not None or _binary_exists(SANDBOX_EXEC_BIN)


def _binary_exists(path: str) -> bool:
    import os

    return os.path.isfile(path) and os.access(path, os.X_OK)


def default_profile(workspace_dir: str) -> str:
    """Return a Seatbelt profile string for an agent operating in a workspace.

    The profile allows arbitrary file reads, network access, process
    fork/exec, and IPC, but restricts writes to the workspace, the standard
    temporary directories, and the user's caches/log dirs. This roughly mirrors
    what other agent runtimes (e.g. Claude Code) ship as a default profile.
    """
    # The workspace path is interpolated as-is. Seatbelt profiles are
    # TinyScheme; embedded double-quotes would be a syntax error. Reject them
    # rather than silently producing an invalid profile.
    if '"' in workspace_dir:
        raise ValueError(
            f"workspace_dir cannot contain a double-quote: {workspace_dir!r}"
        )

    return f"""(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow file-read*)
(allow file-write*
    (subpath "{workspace_dir}")
    (subpath "/tmp")
    (subpath "/private/tmp")
    (subpath "/var/folders")
    (subpath "/private/var/folders")
    (subpath "/dev")
)
(allow network*)
"""


def wrap_with_sandbox_exec(
    command: list[str],
    workspace_dir: str,
    profile: str | None = None,
) -> list[str]:
    """Prefix ``command`` with ``sandbox-exec -p <profile>`` for execution.

    Args:
        command: The command (argv) to run inside the sandbox.
        workspace_dir: Workspace path used to render the default profile.
        profile: Optional explicit profile string. If omitted, the default
            workspace profile is used.

    Returns:
        A new argv list that, when executed, runs ``command`` under Seatbelt.
    """
    sb_profile = profile if profile is not None else default_profile(workspace_dir)
    sb_path = shutil.which("sandbox-exec") or SANDBOX_EXEC_BIN
    return [sb_path, "-p", sb_profile, *command]
