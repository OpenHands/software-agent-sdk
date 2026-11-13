"""Launch trusted backend code only after the server assigns kernel ownership."""

import ctypes
import os
import subprocess
import sys


LAUNCHER_FLAG = "--canvas-backend-launcher"


def launcher_argv(argv: list[str]) -> list[str]:
    # PyInstaller adds this runtime-only attribute; its executable is not Python.
    if getattr(sys, "frozen", False):
        return [sys.executable, LAUNCHER_FLAG, *argv]
    return [sys.executable, "-m", __name__, *argv]


def run_backend(argv: list[str]) -> int:
    if os.name != "nt" or not argv:
        raise RuntimeError("The backend launcher requires Windows and an executable")
    # EOF also covers server death before job assignment, with no backend spawned.
    if sys.stdin.buffer.read(1) != b"G":
        return 1
    # PyInstaller's DLL directory must not override the backend's own runtime.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    return subprocess.run(argv, stdin=subprocess.DEVNULL, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(run_backend(sys.argv[1:]))
