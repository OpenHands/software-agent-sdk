"""Agent terminals run below the agent server's CPU priority."""

import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session, process_priority
from openhands.tools.terminal.terminal.process_priority import (
    TERMINAL_PROCESS_PRIORITY_ENV,
    process_priority_prefix,
    should_lower_process_priority,
)
from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool


SYSTEM = platform.system()
unix_only = pytest.mark.skipif(
    SYSTEM not in ("Linux", "Darwin"), reason="priority prefix is POSIX-only"
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("", True), ("default", True), ("none", False), (" NONE ", False)],
)
def test_should_lower_process_priority(value: str | None, expected: bool) -> None:
    env = {} if value is None else {TERMINAL_PROCESS_PRIORITY_ENV: value}
    assert should_lower_process_priority(env) is expected


def test_prefix_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(process_priority.os.path, "exists", lambda _: True)
    assert process_priority_prefix({}) == ["/usr/sbin/taskpolicy", "-c", "utility"]


def test_prefix_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: "Linux")
    monkeypatch.setattr(process_priority.shutil, "which", lambda _: "/usr/bin/nice")
    assert process_priority_prefix({}) == ["/usr/bin/nice", "-n", "10"]


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_prefix_is_empty_when_opted_out(
    monkeypatch: pytest.MonkeyPatch, system: str
) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: system)
    assert process_priority_prefix({TERMINAL_PROCESS_PRIORITY_ENV: "none"}) == []


def test_prefix_is_empty_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: "Windows")
    assert process_priority_prefix({}) == []


def _priority_probe(out: Path) -> str:
    """Shell command writing the priority of a grandchild of the terminal shell."""
    if SYSTEM == "Darwin":
        # macOS ignores nice; the utility QoS clamp shows up as a lower PRI.
        return f"sh -c 'ps -o pri= -p $$' > {out}"
    probe = "import os; print(os.getpriority(os.PRIO_PROCESS, 0))"
    return f'sh -c "{sys.executable} -c \\"{probe}\\"" > {out}'


def _server_priority() -> int:
    if SYSTEM == "Darwin":
        result = subprocess.run(
            ["ps", "-o", "pri=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(result.stdout)
    return os.getpriority(os.PRIO_PROCESS, 0)


def _assert_lowered(agent: int, server: int) -> None:
    if SYSTEM == "Darwin":
        assert agent < server
    else:
        assert agent == min(19, server + 10)


@unix_only
@pytest.mark.parametrize("terminal_type", ["tmux", "subprocess"])
@pytest.mark.parametrize("opt_out", [False, True])
def test_session_commands_run_at_lower_priority(
    terminal_type: str, opt_out: bool
) -> None:
    env = {TERMINAL_PROCESS_PRIORITY_ENV: "none"} if opt_out else None
    with tempfile.TemporaryDirectory() as work_dir:
        out = Path(work_dir) / "priority.txt"
        session = create_terminal_session(
            work_dir=work_dir,
            terminal_type=terminal_type,  # type: ignore[arg-type]
            env=env,
        )
        session.initialize()
        try:
            obs = session.execute(TerminalAction(command=_priority_probe(out)))
            assert obs.metadata.exit_code == 0, obs.text
        finally:
            session.close()

        agent = int(out.read_text())
    server = _server_priority()
    if opt_out:
        assert agent == server
    else:
        _assert_lowered(agent, server)


@unix_only
def test_pool_panes_run_at_lower_priority() -> None:
    with tempfile.TemporaryDirectory() as work_dir:
        out = Path(work_dir) / "priority.txt"
        pool = TmuxPanePool(work_dir=work_dir, max_panes=1)
        pool.initialize()
        try:
            terminal = pool.checkout()
            terminal.send_keys(_priority_probe(out))
            deadline = time.monotonic() + 10
            while not (out.exists() and out.read_text().strip()):
                assert time.monotonic() < deadline, terminal.read_screen()
                time.sleep(0.1)
            pool.checkin(terminal)
        finally:
            pool.close()

        agent = int(out.read_text())
    _assert_lowered(agent, _server_priority())
