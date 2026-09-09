"""Windows-specific terminal interrupt behavior tests."""

import platform

import psutil
import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session
from openhands.tools.terminal.terminal.terminal_session import TerminalCommandStatus


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="Windows CTRL_BREAK/PowerShell process behavior only applies on Windows",
)


def _wait_for_child_exit(pid: int, timeout: float = 5.0) -> bool:
    """Wait for the process to exit, returning False if it survives the timeout."""
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True  # already gone
    try:
        process.wait(timeout=timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True


def _stop_powershell_process(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass


@pytest.mark.timeout(45)
def test_windows_ctrl_c_interrupt_kills_child_process(tmp_path) -> None:
    """Ctrl-C after a timeout stops the child that kept the command alive.

    The assertion covers only the directly spawned child process, not the full
    Windows process tree; a leaked grandchild would not fail this test.
    """
    pid_path = tmp_path / "child.pid"
    script_path = tmp_path / "wait_on_child.ps1"
    # Use native path for PowerShell (str() gives Windows-style on Windows)
    pid_path_str = str(pid_path)
    script_path_str = str(script_path)
    script_path.write_text(
        "\n".join(
            [
                f"$pidPath = '{pid_path_str}'",
                "$child = Start-Process -FilePath powershell.exe "
                "-ArgumentList '-NoLogo','-NoProfile','-Command',"
                "'Start-Sleep -Seconds 120' -PassThru",
                "Set-Content -LiteralPath $pidPath -Value $child.Id",
                "Wait-Process -Id $child.Id",
            ]
        ),
        encoding="utf-8",
    )

    session = create_terminal_session(
        work_dir=str(tmp_path),
        terminal_type="powershell",
        no_change_timeout_seconds=1,
    )
    child_pid: int | None = None
    child_exited = False
    try:
        session.initialize()

        obs = session.execute(TerminalAction(command=f"& '{script_path_str}'"))

        assert obs.metadata.exit_code == -1
        assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
        assert pid_path.exists()
        child_pid = int(pid_path.read_text(encoding="utf-8").strip())
        assert psutil.pid_exists(child_pid)

        session.execute(TerminalAction(command="C-c", is_input=True, timeout=3))

        child_exited = _wait_for_child_exit(child_pid)
    finally:
        if child_pid is not None:
            _stop_powershell_process(child_pid)
        session.close()

    assert child_exited, (
        "Windows Ctrl-C reported through the terminal did not terminate the "
        "child process that kept the timed-out command alive."
    )
