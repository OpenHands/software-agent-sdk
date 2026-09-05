"""Tests for MCPClient subprocess cleanup safety net.

These tests verify that ``sync_close()`` kills leaked stdio MCP subprocesses
even when the async close path times out or abandons the portal thread
(see issue #4598 / PR #4548).
"""

import os
import subprocess
import time
import typing

from openhands.sdk.mcp.client import MCPClient


def _spawn_child(command: list[str]) -> subprocess.Popen:
    """Spawn a child process in its own session/process group.

    Using ``start_new_session=True`` creates a new process group so that
    killing it with ``os.killpg`` doesn't affect the test runner.
    """
    return subprocess.Popen(command, start_new_session=True)


def test_is_descendant_identifies_child():
    """_is_descendant correctly identifies a child process."""
    proc = _spawn_child(["sleep", "30"])
    try:
        client = MCPClient.__new__(MCPClient)
        assert client._is_descendant(proc.pid, os.getpid()) is True
        # A non-existent PID is not a descendant
        assert client._is_descendant(999999, os.getpid()) is False
    finally:
        proc.kill()
        proc.wait()


def test_scan_child_procs_finds_by_command():
    """_scan_child_procs finds descendant processes by command string."""
    proc = _spawn_child(["sleep", "30"])
    try:
        client = MCPClient.__new__(MCPClient)

        class FakeTransport:
            command: typing.ClassVar[str] = "sleep"
            args: typing.ClassVar[list[str]] = ["30"]

        client.transport = FakeTransport()  # type: ignore[attr-defined]
        pids: list[int] = []
        client._scan_child_procs(pids, "sleep 30", "sleep")
        assert proc.pid in pids
    finally:
        proc.kill()
        proc.wait()


def test_kill_process_group_terminates_subprocess():
    """_kill_process_group kills a live subprocess."""
    proc = _spawn_child(["sleep", "30"])
    try:
        # Verify it's alive
        os.kill(proc.pid, 0)
        client = MCPClient.__new__(MCPClient)
        client._kill_process_group(proc.pid)
        # Should be dead now — give it a moment to exit
        time.sleep(1.0)
        # Use wait() with timeout to confirm it exited
        ret = proc.poll()
        assert ret is not None, "subprocess should have been killed"
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_kill_process_group_idempotent_on_dead_pid():
    """_kill_process_group is a no-op on an already-dead PID."""
    proc = _spawn_child(["sleep", "1"])
    proc.wait()  # Let it exit
    client = MCPClient.__new__(MCPClient)
    # Should not raise
    client._kill_process_group(proc.pid)


def test_get_transport_subprocess_pids_returns_descendants():
    """_get_transport_subprocess_pids finds subprocesses for a stdio transport."""

    class FakeTransport:
        command: typing.ClassVar[str] = "sleep"
        args: typing.ClassVar[list[str]] = ["30"]

    client = MCPClient.__new__(MCPClient)
    client.transport = FakeTransport()  # type: ignore[attr-defined]

    proc = _spawn_child(["sleep", "30"])
    try:
        pids = client._get_transport_subprocess_pids()
        assert proc.pid in pids
    finally:
        proc.kill()
        proc.wait()


def test_get_transport_subprocess_pids_empty_without_transport():
    """_get_transport_subprocess_pids returns empty list if no transport."""
    client = MCPClient.__new__(MCPClient)
    # No transport attribute — should return empty, not raise
    pids = client._get_transport_subprocess_pids()
    assert pids == []


def test_sync_close_kills_leaked_subprocess():
    """sync_close kills subprocesses that survive the async close.

    This simulates the bug: the async close times out (or the portal thread
    is abandoned), leaving the subprocess alive. sync_close's safety net
    should kill it.
    """
    # We can't easily create a real MCPClient with a live transport in a unit
    # test (it needs a real MCP server), so we test the safety-net mechanism
    # directly: verify that _force_kill_subprocesses finds and kills a
    # descendant process matching the transport's command.
    proc = _spawn_child(["sleep", "30"])
    try:

        class FakeTransport:
            command: typing.ClassVar[str] = "sleep"
            args: typing.ClassVar[list[str]] = ["30"]

        client = MCPClient.__new__(MCPClient)
        client.transport = FakeTransport()  # type: ignore[attr-defined]
        client._force_kill_subprocesses()

        time.sleep(1.0)
        ret = proc.poll()
        assert ret is not None, "subprocess should have been killed by safety net"
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
