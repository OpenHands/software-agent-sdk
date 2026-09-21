"""Tests for MCPClient stdio subprocess cleanup."""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from fastmcp.client.transports import StdioTransport

from openhands.sdk.mcp.client import MCPClient


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups only")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SERVER_ARGS = ["-m", "tests.sdk.mcp.stdio_test_server"]


class AbandonedCloseMCPClient(MCPClient):
    async def close(self) -> None:
        """Simulate an async close path that leaves its transport running."""


class RecordingMCPClient(MCPClient):
    forced_pids: Sequence[int] | None = None

    def _force_kill_subprocesses(self, pids: Sequence[int]) -> None:
        self.forced_pids = pids
        super()._force_kill_subprocesses(pids)


class FailingCleanupMCPClient(MCPClient):
    def _force_kill_subprocesses(self, pids: Sequence[int]) -> None:
        raise RuntimeError("forced cleanup failed")


def _client(client_type: type[MCPClient] = MCPClient) -> MCPClient:
    transport = StdioTransport(sys.executable, _SERVER_ARGS, cwd=str(_REPO_ROOT))
    client = client_type(transport)
    client.call_async_from_sync(client.connect, timeout=10.0)
    return client


def _is_alive(pid: int) -> bool:
    try:
        if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            return False
    except FileNotFoundError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_dead(pid: int) -> None:
    deadline = time.monotonic() + 5
    while _is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _is_alive(pid)


def test_sync_close_kills_only_its_owned_stdio_process() -> None:
    """Clients with identical commands must not kill each other's server."""
    first = _client(AbandonedCloseMCPClient)
    second = _client()
    try:
        first_pids = first._get_transport_subprocess_pids()
        second_pids = second._get_transport_subprocess_pids()
        assert len(first_pids) == len(second_pids) == 1
        assert first_pids != second_pids

        first.sync_close()

        _wait_until_dead(first_pids[0])
        assert _is_alive(second_pids[0])
    finally:
        first.sync_close()
        second.sync_close()


def test_sync_close_does_not_force_kill_stale_pid_after_clean_close() -> None:
    """A clean close must not pass its exited process PID to forced cleanup."""
    client = cast(RecordingMCPClient, _client(RecordingMCPClient))

    client.sync_close()

    assert client.forced_pids == []


def test_sync_close_closes_executor_when_forced_cleanup_fails() -> None:
    """Forced subprocess cleanup must not prevent executor teardown."""
    client = cast(FailingCleanupMCPClient, _client(FailingCleanupMCPClient))

    client.sync_close()

    assert client._closed
    assert client._executor._portal is None


def test_process_group_escalates_after_leader_exits() -> None:
    script = """
import signal
import subprocess
import sys
import time

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
])
print(child.pid, flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
time.sleep(60)
"""
    leader = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert leader.stdout is not None
    child_pid = int(leader.stdout.readline())
    try:
        MCPClient._kill_process_group(leader.pid)

        leader.wait(timeout=5)
        _wait_until_dead(child_pid)
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        leader.wait(timeout=5)
