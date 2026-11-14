"""Platform-independent shutdown tests for the PowerShell terminal backend.

They emulate the Windows pipe semantics behind #6469 so the regression is also
covered outside the Windows CI job.
"""

import platform
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import IO, cast

import pytest

from openhands.tools.terminal.terminal import windows_terminal
from openhands.tools.terminal.terminal.windows_terminal import WindowsTerminal


class _WindowsLikePipe:
    """Like a Windows pipe handle, close() waits for an in-flight read."""

    def __init__(self, raw):
        self._raw = raw
        self._cond = threading.Condition()
        self._pending_reads = 0

    def read(self, size: int) -> bytes:
        with self._cond:
            self._pending_reads += 1
        try:
            return self._raw.read(size)
        finally:
            with self._cond:
                self._pending_reads -= 1
                self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            while self._pending_reads:
                self._cond.wait()
        self._raw.close()

    def __getattr__(self, name: str):
        return getattr(self._raw, name)


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Uses a POSIX stand-in shell; test_windows_terminal.py covers PowerShell",
)
def test_close_does_not_deadlock_when_reader_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = tmp_path / "fake_pwsh"
    shell.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        "sys.stdout.write('PS> '); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    shell.chmod(shell.stat().st_mode | stat.S_IXUSR)

    class _Popen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stdout = cast(IO[bytes], _WindowsLikePipe(self.stdout))

    monkeypatch.setattr(windows_terminal.subprocess, "Popen", _Popen)

    terminal = WindowsTerminal(work_dir=str(tmp_path), shell_path=str(shell))
    terminal.initialize()
    assert terminal.process is not None
    proc = terminal.process
    time.sleep(0.3)
    assert terminal.reader_thread is not None and terminal.reader_thread.is_alive()

    closer = threading.Thread(target=terminal.close, daemon=True)
    closer.start()
    closer.join(timeout=10)
    try:
        assert not closer.is_alive(), "close() deadlocked on the blocked stdout read"
        assert terminal.process is None
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_write_to_stdin_is_bounded_when_pipe_blocks(tmp_path: Path) -> None:
    release = threading.Event()

    class _BlockedStdin:
        def write(self, data: bytes) -> int:
            release.wait()
            return len(data)

        def flush(self) -> None:
            pass

    terminal = WindowsTerminal(work_dir=str(tmp_path))
    terminal.process = cast(subprocess.Popen, SimpleNamespace(stdin=_BlockedStdin()))
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            terminal._write_to_stdin("Write-Output 'x'\n", timeout=0.2)
        assert time.monotonic() - start < 2.0
    finally:
        release.set()
        terminal.process = None
        terminal._closed = True
