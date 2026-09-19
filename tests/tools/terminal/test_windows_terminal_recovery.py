"""Regression tests for Windows PowerShell terminal recovery.

These pin the invariant that a failed, timed-out, or interrupted command
never leaves the session unable to run the next command.  They run only on
Windows because they exercise the real PowerShell backend, Win32 Ctrl+C
delivery, and job-object process-tree termination.

See OpenHands/software-agent-sdk (Windows terminal recovery) and the
corroborating product report OpenHands/OpenHands#17198.
"""

import os
import platform
import subprocess
import tempfile
import time
from collections.abc import Generator

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.terminal import create_terminal_session
from openhands.tools.terminal.terminal.terminal_session import TerminalCommandStatus


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="Windows PowerShell recovery behavior only applies on Windows",
)

NO_CHANGE = 2


@pytest.fixture
def work_dir() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
    ).stdout
    return str(pid) in out


def _wait_pid_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _ready(session) -> None:
    obs = session.execute(TerminalAction(command="Write-Output READY-CHECK"))
    assert "READY-CHECK" in obs.text
    assert obs.metadata.exit_code == 0
    assert session.prev_status == TerminalCommandStatus.COMPLETED


# --------------------------------------------------------------------------- #
# Completion detection: exit code survives however the command ends
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("command", "expected_exit"),
    [
        pytest.param("cmd /c exit 7", 7, id="native-nonzero-exit"),
        pytest.param(
            "Get-Item C:\\no\\such\\path -ErrorAction Stop", 1, id="terminating-error"
        ),
        pytest.param("throw 'boom'", 1, id="throw"),
        pytest.param(
            "$ErrorActionPreference='Stop'; Get-Item C:\\no\\such -ErrorAction Stop",
            1,
            id="error-action-preference-stop",
        ),
        pytest.param("Write-Host -NoNewline no-trailing-newline", 0, id="no-newline"),
    ],
)
def test_completion_detected_however_command_ends(
    work_dir: str, command: str, expected_exit: int
) -> None:
    """A prompt-level sentinel reappears even when the command line aborts.

    The previous design appended the sentinel to the command line, so a
    terminating error skipped it and the session waited out the full
    no-change timeout on a command that had actually finished.
    """
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    try:
        session.initialize()
        start = time.time()
        obs = session.execute(TerminalAction(command=command))
        assert obs.metadata.exit_code == expected_exit, obs.metadata
        assert session.prev_status == TerminalCommandStatus.COMPLETED
        assert time.time() - start < NO_CHANGE, "completion was not detected promptly"
        _ready(session)
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Interrupt recovers the session and kills the process subtree
# --------------------------------------------------------------------------- #
def test_ctrl_c_of_builtin_makes_session_ready(work_dir: str) -> None:
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    try:
        session.initialize()
        obs = session.execute(TerminalAction(command="Start-Sleep 120; 'never'"))
        assert obs.metadata.exit_code == -1
        assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
        session.execute(TerminalAction(command="C-c", is_input=True))
        assert session.prev_status not in (
            TerminalCommandStatus.NO_CHANGE_TIMEOUT,
            TerminalCommandStatus.HARD_TIMEOUT,
        )
        _ready(session)
    finally:
        session.close()


def test_ctrl_c_kills_child_that_ignores_sigint(work_dir: str) -> None:
    """Ctrl+C alone cannot stop a SIGINT-ignoring child; the tree kill must."""
    pid_path = os.path.join(work_dir, "child.pid")
    script = os.path.join(work_dir, "ignore_sigint.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(
            "import os, signal, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            f"open(r'{pid_path}', 'w').write(str(os.getpid()))\n"
            "time.sleep(120)\n"
        )
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    child_pid: int | None = None
    try:
        session.initialize()
        obs = session.execute(TerminalAction(command=f'& "{_python()}" "{script}"'))
        assert obs.metadata.exit_code == -1
        child_pid = _read_pid(pid_path)
        assert _pid_alive(child_pid)
        session.execute(TerminalAction(command="C-c", is_input=True, timeout=10))
        assert _wait_pid_dead(child_pid), "SIGINT-ignoring child survived interrupt"
        _ready(session)
    finally:
        if child_pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/F"], capture_output=True
            )
        session.close()


def test_interrupted_session_does_not_poison_later_commands(work_dir: str) -> None:
    """Regression for OpenHands/OpenHands#17198.

    Symptom: after one command is interrupted, *every* later command is
    rejected with "previous command is still running" (or returns an empty
    observation), so the agent reports the terminal is stuck and retries
    forever.  Here we interrupt a long command and then run a batch of
    ordinary commands; all of them must execute normally.
    """
    os.makedirs(os.path.join(work_dir, ".agents", "agents"), exist_ok=True)
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    try:
        session.initialize()
        # Poison attempt: long command -> soft timeout -> Ctrl+C.
        assert (
            session.execute(
                TerminalAction(command="Start-Sleep 120")
            ).metadata.exit_code
            == -1
        )
        session.execute(TerminalAction(command="C-c", is_input=True))
        # #17198's exact command plus ordinary neighbours must all run.
        for cmd in (
            "Get-ChildItem .agents/agents/",
            "Write-Output first",
            "Get-ChildItem .agents/agents/",
            "Write-Output second",
        ):
            obs = session.execute(TerminalAction(command=cmd))
            suffix = obs.metadata.suffix
            assert "previous command is still running" not in suffix, (
                f"{cmd!r} rejected as still-running (#17198): {suffix!r}"
            )
            assert obs.metadata.exit_code != -1, f"{cmd!r} never completed"
        _ready(session)
    finally:
        session.close()


def test_immediate_command_after_interrupt_is_not_rejected(work_dir: str) -> None:
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    try:
        session.initialize()
        assert (
            session.execute(
                TerminalAction(command="Start-Sleep 120")
            ).metadata.exit_code
            == -1
        )
        session.execute(TerminalAction(command="C-c", is_input=True))
        obs = session.execute(TerminalAction(command="Write-Output immediately-after"))
        assert "immediately-after" in obs.text
        assert "NOT executed" not in obs.metadata.suffix
        assert obs.metadata.exit_code == 0
    finally:
        session.close()


def test_repeated_and_idle_interrupts_are_idempotent(work_dir: str) -> None:
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    try:
        session.initialize()
        session.execute(TerminalAction(command="Start-Sleep 120"))
        for _ in range(3):
            session.execute(TerminalAction(command="C-c", is_input=True))
        _ready(session)
        # a Ctrl+C with nothing running must be harmless
        session.execute(TerminalAction(command="C-c", is_input=True))
        _ready(session)
    finally:
        session.close()


def test_input_after_command_finished_returns_output_not_rejection(
    work_dir: str,
) -> None:
    """A timed-out command that finishes on its own must not wedge the next input."""
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=NO_CHANGE,
    )
    try:
        session.initialize()
        obs = session.execute(
            TerminalAction(command="Start-Sleep 3; Write-Output finished-alone")
        )
        assert obs.metadata.exit_code == -1
        time.sleep(2.5)  # command completes with nobody polling
        obs2 = session.execute(TerminalAction(command="", is_input=True))
        assert "finished-alone" in obs2.text
        assert obs2.metadata.exit_code == 0
        _ready(session)
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Self-healing at the executor level
# --------------------------------------------------------------------------- #
def test_executor_recreates_session_after_shell_exits(work_dir: str) -> None:
    """`exit` kills the shell; the next command must still run (new session)."""
    ex = TerminalExecutor(
        working_dir=work_dir,
        no_change_timeout_seconds=NO_CHANGE,
        terminal_type="powershell",
    )
    try:
        ex(TerminalAction(command="exit"))
        obs = ex(TerminalAction(command="Write-Output alive-again"))
        assert "alive-again" in obs.text
        assert obs.metadata.exit_code == 0
    finally:
        ex.close()


def test_executor_recreates_session_after_shell_killed(work_dir: str) -> None:
    ex = TerminalExecutor(
        working_dir=work_dir,
        no_change_timeout_seconds=NO_CHANGE,
        terminal_type="powershell",
    )
    try:
        pid_obs = ex(TerminalAction(command="Write-Output $PID"))
        pid = int(pid_obs.text.strip().splitlines()[-1])
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        assert _wait_pid_dead(pid, 5)
        obs = ex(TerminalAction(command="Write-Output healed"))
        assert "healed" in obs.text
        assert obs.metadata.exit_code == 0
    finally:
        ex.close()


# --------------------------------------------------------------------------- #
# Reset preserves configured env; sheds interactive env
# --------------------------------------------------------------------------- #
def test_reset_reinjects_configured_env_but_not_interactive(work_dir: str) -> None:
    ex = TerminalExecutor(
        working_dir=work_dir,
        no_change_timeout_seconds=NO_CHANGE,
        terminal_type="powershell",
        env={"OH_CONFIGURED": "configured-value"},
    )
    try:
        ex(TerminalAction(command="$env:OH_INTERACTIVE = 'interactive-value'"))
        obs = ex(TerminalAction(command="", reset=True))
        assert "reset" in obs.text.lower()
        obs = ex(
            TerminalAction(
                command='Write-Output "c=$env:OH_CONFIGURED i=$env:OH_INTERACTIVE"'
            )
        )
        assert "c=configured-value" in obs.text, obs.text
        assert "i=interactive-value" not in obs.text, obs.text
    finally:
        ex.close()


def _python() -> str:
    import sys

    return sys.executable


def _read_pid(path: str, timeout: float = 10.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = open(path).read().strip()
            if data:
                return int(data)
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise AssertionError("child never wrote its pid")
