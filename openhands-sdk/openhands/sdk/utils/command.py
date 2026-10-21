import os
import shlex
import subprocess
import sys
import threading
from collections.abc import Mapping
from typing import Final

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.redact import redact_text_secrets


logger = get_logger(__name__)


# Env vars that should not be exposed to subprocesses (e.g., bash commands
# executed by the agent). These credentials allow access to user secrets via
# the SaaS API and/or decrypting persisted secrets, and must remain isolated to
# the SDK's Python process.
#
# - ``SESSION_API_KEY``: legacy (V0) session key name.
# - ``OH_SECRET_KEY``: cipher key that decrypts persisted conversation/provider
#   secrets; leaking it is at least as damaging as leaking the session key.
# See ``openhands.agent_server.config`` for where these are read from the env.
_SENSITIVE_ENV_VARS = frozenset({"SESSION_API_KEY", "OH_SECRET_KEY"})

# Session keys are also delivered as an indexed list ``OH_SESSION_API_KEYS_0``,
# ``OH_SESSION_API_KEYS_1``, ... (V1). Strip every slot by prefix so a rename or
# an added rotation key cannot silently re-expose the credential to subprocesses.
_SENSITIVE_ENV_PREFIXES: tuple[str, ...] = ("OH_SESSION_API_KEYS_",)
_AI_AGENT_ENV_VAR: Final[str] = "AI_AGENT"


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

    ``AI_AGENT`` defaults to ``openhands`` so downstream tools can select
    agent-friendly output without relying on product-specific heuristics.
    """

    base_env: dict[str, str]
    if env is None:
        base_env = dict(os.environ)
    else:
        base_env = dict(env)

    # Strip sensitive env vars to prevent agent access via bash commands
    for key in _SENSITIVE_ENV_VARS:
        base_env.pop(key, None)

    # Strip indexed / prefixed credential slots (e.g. OH_SESSION_API_KEYS_0..N).
    for key in [
        k
        for k in base_env
        if any(k.startswith(prefix) for prefix in _SENSITIVE_ENV_PREFIXES)
    ]:
        base_env.pop(key, None)

    if not base_env.get(_AI_AGENT_ENV_VAR, "").strip():
        base_env[_AI_AGENT_ENV_VAR] = "openhands"

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
