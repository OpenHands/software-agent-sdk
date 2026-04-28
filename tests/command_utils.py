"""Portable command builders for tests that execute through a shell."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def shell_join(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def python_command(script: str) -> str:
    return shell_join([sys.executable, "-c", script])


def touch_command(path: str | Path) -> str:
    return python_command(f"from pathlib import Path; Path({str(path)!r}).touch()")


def sleep_command(seconds: float) -> str:
    return python_command(f"import time; time.sleep({seconds})")
