"""A terminating PowerShell error must not poison the session (OpenHands#17198).

Runs wherever PowerShell 7 (``pwsh``) is installed, including Linux CI, so the
completion-marker logic is covered outside the Windows job too.
"""

import shutil
import tempfile
from collections.abc import Generator

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session


pytestmark = pytest.mark.skipif(
    shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not installed"
)


@pytest.fixture
def session() -> Generator:
    with tempfile.TemporaryDirectory() as work_dir:
        s = create_terminal_session(
            work_dir=work_dir,
            terminal_type="powershell",
            shell_path=shutil.which("pwsh"),
            no_change_timeout_seconds=5,
        )
        s.initialize()
        try:
            yield s
        finally:
            s.close()


@pytest.mark.parametrize(
    "failing_command",
    [
        "throw 'boom'",
        "Get-Item -Path missing.txt -ErrorAction Stop",
        "Write-Error 'bad' -ErrorAction Stop",
    ],
)
def test_terminating_error_completes_and_next_command_runs(session, failing_command):
    failed = session.execute(TerminalAction(command=failing_command))
    assert failed.metadata.exit_code == 1
    assert "still running" not in (failed.metadata.suffix or "")

    after = session.execute(TerminalAction(command="Write-Output still-usable"))
    assert "still-usable" in after.text
    assert after.metadata.exit_code == 0


def test_parse_error_completes_and_next_command_runs(session):
    failed = session.execute(TerminalAction(command="Write-Output (1 +* 2)"))
    assert "still running" not in (failed.metadata.suffix or "")
    assert "value expression" in failed.text

    after = session.execute(TerminalAction(command="Write-Output still-usable"))
    assert "still-usable" in after.text
    assert after.metadata.exit_code == 0
