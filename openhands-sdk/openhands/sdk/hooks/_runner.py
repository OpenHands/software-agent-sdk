"""Persistent hook runner — lightweight subprocess that proxies hook calls.

Launched once per ``HookExecutor`` session, this process reads JSON-line
requests on *stdin*, executes each hook command via ``subprocess.run``, and
writes JSON-line responses on *stdout*.  Because the runner's heap is tiny
compared to the main SDK/server process, each internal ``fork+exec`` is
substantially cheaper (fewer COW page-table entries, smaller RSS to clone).

Protocol
--------
**Request** (one JSON object per line on *stdin*)::

    {
        "command": "check_safety.sh",
        "cwd": "/project",
        "env": {"KEY": "val", ...},
        "input": "<event JSON>",
        "timeout": 60
    }

**Response** (one JSON object per line on *stdout*)::

    {
        "exit_code": 0,
        "stdout": "...",
        "stderr": "...",
        "error": null,
        "timed_out": false
    }

The runner exits cleanly when *stdin* is closed (EOF).
"""

from __future__ import annotations

import json
import subprocess
import sys


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_response(exit_code=-1, error=f"bad request JSON: {exc}")
            continue

        command: str = request.get("command", "")
        cwd: str | None = request.get("cwd")
        env: dict[str, str] | None = request.get("env")
        stdin_data: str = request.get("input", "")
        timeout: int = request.get("timeout", 60)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            _write_response(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except subprocess.TimeoutExpired:
            _write_response(
                exit_code=-1,
                error=f"hook timed out after {timeout}s",
                timed_out=True,
            )
        except FileNotFoundError as exc:
            _write_response(exit_code=-1, error=f"command not found: {exc}")
        except Exception as exc:  # noqa: BLE001
            _write_response(exit_code=-1, error=f"execution failed: {exc}")


def _write_response(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    error: str | None = None,
    timed_out: bool = False,
) -> None:
    payload = {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
        "timed_out": timed_out,
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
