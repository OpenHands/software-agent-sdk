"""Run inside the packaged server through its custom module import option."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session


assert getattr(sys, "frozen", False), "Run this with the PyInstaller server executable"
result = {
    "frozen": True,
    "executable": Path(sys.executable).name,
    "python_on_path": shutil.which("python"),
    "checks": [],
}
with tempfile.TemporaryDirectory() as work_dir:
    session = create_terminal_session(
        work_dir=work_dir,
        terminal_type="powershell",
        no_change_timeout_seconds=1,
    )
    try:
        session.initialize()
        initial = session.execute(
            TerminalAction(command="$env:OH_SMOKE_STATE='preserved'; Write-Output $PID")
        )
        shell_pid = initial.metadata.pid
        for command, expected in (
            ("throw 'expected-error'", 1),
            ("Write-Output before-interrupt", 0),
        ):
            observation = session.execute(TerminalAction(command=command))
            result["checks"].append(
                {"command": command, "exit_code": observation.metadata.exit_code}
            )
            assert observation.metadata.exit_code == expected
        waiting = session.execute(TerminalAction(command="Start-Sleep 120"))
        assert waiting.metadata.exit_code == -1
        session.execute(TerminalAction(command="C-c", is_input=True, timeout=20))
        recovered = session.execute(
            TerminalAction(
                command="Write-Output $env:OH_SMOKE_STATE; Write-Output AFTER_INTERRUPT"
            )
        )
        result["state_preserved"] = "preserved" in recovered.text
        result["same_shell"] = recovered.metadata.pid == shell_pid
        result["exit_code"] = recovered.metadata.exit_code
        result["recovered"] = "AFTER_INTERRUPT" in recovered.text
        assert (
            result["state_preserved"] and result["same_shell"] and result["recovered"]
        )
        assert result["exit_code"] == 0
    finally:
        session.close()
print(json.dumps(result, sort_keys=True))
raise SystemExit(0)
