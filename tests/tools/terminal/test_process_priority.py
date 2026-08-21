"""Cross-platform process priority coverage for agent terminals."""

import platform
from pathlib import Path
from typing import Literal

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import process_priority
from openhands.tools.terminal.terminal.factory import create_terminal_session


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        pytest.param(
            "Darwin",
            ("/usr/sbin/taskpolicy", "-c", "utility"),
            id="macos-utility-qos",
        ),
        pytest.param("Linux", ("nice", "-n", "10"), id="linux-niceness"),
        pytest.param("Windows", (), id="windows-uses-creation-flags"),
        pytest.param("FreeBSD", (), id="unsupported-platform"),
    ],
)
def test_process_priority_prefix_matches_platform(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    expected: tuple[str, ...],
) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: system)

    assert process_priority.get_process_priority_prefix() == expected


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="Linux niceness is only observable on Linux",
)
@pytest.mark.parametrize("terminal_type", ["tmux", "subprocess"])
def test_linux_terminal_children_inherit_lower_priority(
    tmp_path: Path,
    terminal_type: Literal["tmux", "subprocess"],
) -> None:
    session = create_terminal_session(
        work_dir=str(tmp_path),
        terminal_type=terminal_type,
    )
    try:
        session.initialize()
        observation = session.execute(
            TerminalAction(
                command=(
                    "python -c 'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'"
                )
            )
        )
    finally:
        session.close()

    assert observation.exit_code == 0
    assert int(observation.text.strip()) >= 10
