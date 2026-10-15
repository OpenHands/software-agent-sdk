"""MCPClient.sync_close kills the stdio processes that client owns."""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastmcp.client.transports import StdioTransport

from openhands.sdk.mcp.client import MCPClient


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups only")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROBE = ["-m", "tests.sdk.mcp.stdio_cleanup_probe"]


class _AbandonedClose(MCPClient):
    async def close(self) -> None:
        """Leave the transport running, as a timed-out async close does."""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is gone for the purposes of this cleanup.
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    return stat != "Z"


def _wait_until_dead(pid: int) -> None:
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def _read_pid(path: Path) -> int:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"pid file missing: {path}")
        time.sleep(0.05)
    return int(path.read_text())


def _reap(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _open(
    tmp_path: Path,
    name: str,
    *,
    spawn_child: bool = False,
    abandoned: bool = False,
) -> MCPClient:
    env = os.environ.copy()
    env["OH_MCP_PIDFILE"] = str(tmp_path / f"{name}.pid")
    env["OH_MCP_CHILD_PIDFILE"] = str(tmp_path / f"{name}-child.pid")
    env["OH_MCP_CHILD_READY"] = str(tmp_path / f"{name}-child.ready")
    if spawn_child:
        env["OH_MCP_SPAWN_CHILD"] = "1"
    transport = StdioTransport(sys.executable, _PROBE, env=env, cwd=str(_REPO_ROOT))
    client_type = _AbandonedClose if abandoned else MCPClient
    client = client_type(transport)
    client.call_async_from_sync(client.connect, timeout=10.0)
    return client


def test_sync_close_kills_only_its_owned_stdio_process(tmp_path: Path) -> None:
    """An abandoned close kills this client's server and leaves a twin running."""
    first = _open(tmp_path, "first", abandoned=True)
    second = _open(tmp_path, "second")
    first_pid = _read_pid(tmp_path / "first.pid")
    second_pid = _read_pid(tmp_path / "second.pid")
    try:
        assert _alive(first_pid)
        assert _alive(second_pid)

        first.sync_close()
        first.sync_close()

        _wait_until_dead(first_pid)
        assert _alive(second_pid)
    finally:
        first.sync_close()
        second.sync_close()
        _reap(first_pid)
        _reap(second_pid)


def test_clean_sync_close_spares_an_unrelated_process(tmp_path: Path) -> None:
    """A finished close kills its server, and a second call leaves other pids alone."""
    client = _open(tmp_path, "clean")
    server_pid = _read_pid(tmp_path / "clean.pid")
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    try:
        client.sync_close()
        client.sync_close()

        _wait_until_dead(server_pid)
        assert bystander.poll() is None
    finally:
        client.sync_close()
        _reap(server_pid)
        if bystander.poll() is None:
            bystander.kill()
            bystander.wait(timeout=5)


def test_sync_close_kills_descendant_that_ignores_sigterm(tmp_path: Path) -> None:
    """SIGKILL reaches a child that survives the group's SIGTERM."""
    client = _open(tmp_path, "group", spawn_child=True, abandoned=True)
    leader_pid = _read_pid(tmp_path / "group.pid")
    child_pid = _read_pid(tmp_path / "group-child.pid")
    try:
        assert _alive(leader_pid)
        assert _alive(child_pid)

        client.sync_close()

        _wait_until_dead(leader_pid)
        _wait_until_dead(child_pid)
    finally:
        client.sync_close()
        _reap(leader_pid)
        _reap(child_pid)


def test_sync_close_kills_descendant_after_leader_exits(tmp_path: Path) -> None:
    """A wrapper that has already exited does not hide its descendant."""
    client = _open(tmp_path, "reaped", abandoned=True)
    leader_pid = _read_pid(tmp_path / "reaped.pid")
    try:
        with contextlib.suppress(Exception):
            client.call_async_from_sync(client.call_tool, "release", timeout=5.0)
        child_pid = _read_pid(tmp_path / "reaped-child.pid")
        _wait_until_dead(leader_pid)
        assert _alive(child_pid)

        client.sync_close()

        _wait_until_dead(child_pid)
    finally:
        client.sync_close()
        _reap(leader_pid)
        child_file = tmp_path / "reaped-child.pid"
        if child_file.exists():
            _reap(int(child_file.read_text()))
