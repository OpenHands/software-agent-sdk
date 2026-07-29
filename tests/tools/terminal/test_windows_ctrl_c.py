"""Windows-specific terminal interrupt behavior tests."""

import platform
import subprocess

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session
from openhands.tools.terminal.terminal.terminal_session import TerminalCommandStatus


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="Windows CTRL_BREAK/PowerShell process behavior only applies on Windows",
)


def _powershell_process_exists(pid: int) -> bool:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            (
                f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
                "{ exit 0 } else { exit 1 }"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _stop_powershell_process(pid: int) -> None:
    subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.timeout(20)
def test_windows_ctrl_c_interrupt_kills_child_process_tree(tmp_path) -> None:
    """Ctrl-C after a timeout should stop the process that kept the command alive.

    This captures the behavior promised by the timeout prompt. The current
    PowerShell backend sends CTRL_BREAK to the persistent PowerShell process, but
    does not ensure child processes launched by the command are terminated.
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
    child_was_still_running = False
    try:
        session.initialize()

        obs = session.execute(TerminalAction(command=f"& '{script_path_str}'"))

        assert obs.metadata.exit_code == -1
        assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
        assert pid_path.exists()
        child_pid = int(pid_path.read_text(encoding="utf-8").strip())
        assert _powershell_process_exists(child_pid)

        session.execute(TerminalAction(command="C-c", is_input=True, timeout=3))

        child_was_still_running = _powershell_process_exists(child_pid)
    finally:
        if child_pid is not None:
            _stop_powershell_process(child_pid)
        session.close()

    assert not child_was_still_running, (
        "Windows Ctrl-C reported through the terminal did not terminate the "
        "child process that kept the timed-out command alive."
    )
