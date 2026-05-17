import os
import shlex
import subprocess
import sys
import threading
from collections.abc import Mapping

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.redact import redact_text_secrets


logger = get_logger(__name__)


# Env vars that should not be exposed to subprocesses (e.g., bash commands
# executed by the agent). These credentials allow access to user secrets via
# the SaaS API and must remain isolated to the SDK's Python process.
_SENSITIVE_ENV_VARS = frozenset({"SESSION_API_KEY"})


def sanitized_env(
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a copy of *env* with sanitized values.

    PyInstaller-based binaries rewrite ``LD_LIBRARY_PATH`` so their vendored
    libraries win. This function restores the original value so that subprocess
    will not use them.

    Sensitive environment variables (e.g., ``SESSION_API_KEY``) are stripped
    to prevent LLM-driven agents from accessing credentials via terminal
    commands.
    """

    base_env: dict[str, str]
    if env is None:
        base_env = dict(os.environ)
    else:
        base_env = dict(env)

    # Strip sensitive env vars to prevent agent access via bash commands
    for key in _SENSITIVE_ENV_VARS:
        base_env.pop(key, None)

    if "LD_LIBRARY_PATH_ORIG" in base_env:
        origin = base_env["LD_LIBRARY_PATH_ORIG"]
        if origin:
            base_env["LD_LIBRARY_PATH"] = origin
        else:
            base_env.pop("LD_LIBRARY_PATH", None)
    return base_env


def execute_command(
    cmd: list[str] | str,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float | None = None,
    print_output: bool = True,
) -> subprocess.CompletedProcess:
    # For string commands, use shell=True to handle shell operators properly
    if isinstance(cmd, str):
        cmd_to_run = cmd
        use_shell = True
        cmd_str = cmd
    else:
        cmd_to_run = cmd
        use_shell = False
        cmd_str = " ".join(shlex.quote(c) for c in cmd)

    # Log the command with sensitive values redacted
    logger.info("$ %s", redact_text_secrets(cmd_str))

    proc = subprocess.Popen(
        cmd_to_run,
        cwd=cwd,
        env=sanitized_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        shell=use_shell,
    )
    if proc is None:
        raise RuntimeError("Failed to start process")

    # Read line by line, echo to parent stdout/stderr
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    if proc.stdout is None or proc.stderr is None:
        raise RuntimeError("Failed to capture stdout/stderr")

    def read_stream(stream, lines, output_stream):
        try:
            for line in stream:
                if print_output:
                    output_stream.write(line)
                    output_stream.flush()
                lines.append(line)
        except Exception as e:
            logger.error(f"Failed to read stream: {e}")

    # Read stdout and stderr concurrently to avoid deadlock
    stdout_thread = threading.Thread(
        target=read_stream, args=(proc.stdout, stdout_lines, sys.stdout)
    )
    stderr_thread = threading.Thread(
        target=read_stream, args=(proc.stderr, stderr_lines, sys.stderr)
    )

    stdout_thread.start()
    stderr_thread.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_thread.join()
        stderr_thread.join()
        return subprocess.CompletedProcess(
            cmd_to_run,
            -1,  # Indicate timeout with -1 exit code
            "".join(stdout_lines),
            "".join(stderr_lines),
        )

    stdout_thread.join(timeout=timeout)
    stderr_thread.join(timeout=timeout)

    return subprocess.CompletedProcess(
        cmd_to_run,
        proc.returncode,
        "".join(stdout_lines),
        "".join(stderr_lines),
    )
