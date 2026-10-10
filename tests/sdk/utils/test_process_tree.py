"""Tests for ProcessTreeGuard.

The behaviour that matters — a Job Object reaping grandchildren — can only be
observed on Windows, so that assertion lives in a platform-gated test. The rest
runs everywhere so the contract stays covered by the Linux jobs.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

from openhands.sdk.utils.process_tree import ProcessTreeGuard


class TestForProcessContract:
    def test_returns_none_off_windows(self, monkeypatch: pytest.MonkeyPatch):
        """Callers keep their existing single-PID teardown on POSIX, where a
        child dies with its parent's stdio pipes anyway."""
        monkeypatch.setattr(sys, "platform", "linux")
        assert ProcessTreeGuard.for_process(1234) is None

    def test_close_is_idempotent(self):
        """_shutdown_runtime may run more than once (close() after _cleanup()),
        so a second close must not raise or double-free the handle."""
        guard = ProcessTreeGuard(0)
        guard.close()
        guard.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
    def test_returns_none_for_nonexistent_pid(self):
        """OpenProcess fails; setup degrades to None rather than raising into
        the spawn path."""
        assert ProcessTreeGuard.for_process(0xFFFFFFF) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
class TestReapsProcessTree:
    """The actual defect: terminate() is single-PID TerminateProcess, so a
    grandchild outlives it."""

    CHILD = textwrap.dedent(
        """
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        time.sleep(120)
        """
    )

    def _spawn_tree(self):
        parent = subprocess.Popen(
            [sys.executable, "-c", self.CHILD],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process | Where-Object "
                    f"{{ $_.ParentProcessId -eq {parent.pid} }} | "
                    "Select-Object -ExpandProperty ProcessId",
                ],
                capture_output=True,
                text=True,
            ).stdout.split()
            if out:
                return parent, int(out[0])
            time.sleep(0.5)
        parent.kill()
        pytest.skip("child process did not start in time")

    @staticmethod
    def _alive(pid: int) -> bool:
        return (
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null",
                ],
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .lower()
            .startswith("true")
        )

    # Note: there is deliberately no "terminate() alone orphans the grandchild"
    # test here. A synthetic python-spawns-python tree does not reliably
    # reproduce the orphaning — the real case involves wrapper layers (npx/cmd)
    # and a provider CLI that outlives them. That before/after was measured
    # against the real ACP server and is recorded in the PR description; a
    # flaky assertion about platform behaviour would not add to it.

    def test_guard_close_reaps_the_grandchild(self):
        parent, child = self._spawn_tree()
        guard = ProcessTreeGuard.for_process(parent.pid)
        assert guard is not None, "job object should be creatable"
        try:
            parent.terminate()
            parent.wait(timeout=10)
            guard.close()
            deadline = time.time() + 15
            while time.time() < deadline and self._alive(child):
                time.sleep(0.5)
            assert not self._alive(child), "grandchild survived the job close"
        finally:
            subprocess.run(
                ["taskkill", "/PID", str(child), "/F"],
                capture_output=True,
            )
