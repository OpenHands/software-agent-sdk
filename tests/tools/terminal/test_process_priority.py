"""Cross-platform process priority coverage for agent terminals."""

import platform
import re
from pathlib import Path
from typing import Literal

import psutil
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
        pytest.param("Linux", ("/usr/bin/nice", "-n", "10"), id="linux-niceness"),
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
    monkeypatch.setattr(
        process_priority.shutil, "which", lambda command: f"/usr/bin/{command}"
    )
    monkeypatch.setattr(process_priority.os.path, "abspath", lambda path: path)

    assert process_priority.get_process_priority_prefix() == expected


def test_linux_priority_falls_back_when_nice_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: "Linux")
    monkeypatch.setattr(process_priority.shutil, "which", lambda _command: None)

    assert process_priority.get_process_priority_prefix() == ()


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_none_setting_disables_process_priority_policy(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
) -> None:
    monkeypatch.setattr(process_priority.platform, "system", lambda: system)
    monkeypatch.setattr(process_priority.shutil, "which", lambda _: "/usr/bin/nice")
    env = {process_priority.TERMINAL_PROCESS_PRIORITY_ENV: "none"}

    assert process_priority.get_process_priority_prefix(env) == ()


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
    parent_priority = int(psutil.Process().nice())
    try:
        session.initialize()
        observation = session.execute(
            TerminalAction(
                command=(
                    "python -c 'import os; "
                    'print("OH_PRIORITY=" + '
                    "str(os.getpriority(os.PRIO_PROCESS, 0)))'"
                )
            )
        )
    finally:
        session.close()

    assert observation.exit_code == 0
    match = re.search(r"OH_PRIORITY=(-?\d+)", observation.text)
    assert match is not None, observation.text
    expected_priority = min(19, parent_priority + 10)
    assert int(match.group(1)) >= expected_priority
