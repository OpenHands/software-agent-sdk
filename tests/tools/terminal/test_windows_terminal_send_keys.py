"""Byte-level coverage for ``WindowsTerminal.send_keys``.

These tests drive the real ``send_keys()`` against a stubbed PowerShell process,
so they run on every platform rather than only in the Windows CI job. They pin
the newline contract the multiline fix depends on: PowerShell holds a multiline
statement at the ``>>`` continuation prompt until it receives a blank line, so
multiline input must be terminated with ``\\n\\n`` while single-line input keeps
exactly one ``\\n``.
"""

import tempfile
from typing import Any, cast

import pytest

from openhands.tools.terminal.terminal.windows_terminal import WindowsTerminal


class _FakeStdin:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        return None


class _FakeProcess:
    """Minimal stand-in for a live PowerShell process."""

    def __init__(self) -> None:
        self.stdin = _FakeStdin()

    def poll(self) -> int | None:
        return None


def _sent_text(command: str, enter: bool = True) -> str:
    """Return exactly what ``send_keys`` writes to PowerShell's stdin."""
    with tempfile.TemporaryDirectory() as work_dir:
        terminal = WindowsTerminal(work_dir=work_dir, shell_path="powershell.exe")
        process = _FakeProcess()
        terminal.process = cast(Any, process)
        terminal.send_keys(command, enter=enter)
        return process.stdin.written.decode("utf-8")


MULTILINE_COMMANDS = [
    pytest.param('Write-Output "a\nb"', id="newline-in-string"),
    pytest.param('if ($true) {\n    Write-Output "yes"\n}', id="if-block"),
    pytest.param('foreach ($i in 1..2) {\n    Write-Output "n$i"\n}', id="foreach"),
    pytest.param('Write-Output `\n    "cont"', id="backtick-continuation"),
    pytest.param('1..3 |\n    ForEach-Object { "v$_" }', id="pipeline"),
    pytest.param('Write-Output "a\r\nb"', id="crlf-newline"),
]

SINGLE_LINE_COMMANDS = [
    pytest.param("Get-ChildItem", id="bare-cmdlet"),
    pytest.param('Write-Output "hello"', id="quoted-arg"),
    pytest.param("Get-Process | Select-Object -First 1", id="single-line-pipeline"),
]


@pytest.mark.parametrize("command", MULTILINE_COMMANDS)
def test_multiline_input_ends_with_blank_line(command: str) -> None:
    """A blank line is what submits PowerShell's continuation buffer."""
    sent = _sent_text(command)

    assert sent.endswith("\n\n")


@pytest.mark.parametrize("command", SINGLE_LINE_COMMANDS)
def test_single_line_input_ends_with_exactly_one_newline(command: str) -> None:
    """The single-line path is unchanged: one newline, no spurious blank line."""
    sent = _sent_text(command)

    assert sent.endswith("\n")
    assert not sent.endswith("\n\n")


@pytest.mark.parametrize("command", MULTILINE_COMMANDS + SINGLE_LINE_COMMANDS)
def test_command_is_submitted_verbatim_without_a_suffix(command: str) -> None:
    """The metadata block comes from the prompt function, not from the command.

    A suffix appended to the command line is lost whenever the line does not
    run to completion (Ctrl+C, ``throw``, ``-ErrorAction Stop``), so nothing
    may be attached to what the model asked to run.
    """
    sent = _sent_text(command)

    assert sent.rstrip("\n") == command.rstrip()
    assert "###PS1" not in sent


def test_init_script_never_echoes_the_markers() -> None:
    """PowerShell echoes every line read from a pipe.

    If the init script contained the literal markers, the echo of the script
    itself would be mistaken for a prompt.
    """
    from openhands.tools.terminal.constants import (
        CMD_OUTPUT_PS1_BEGIN,
        CMD_OUTPUT_PS1_END,
    )
    from openhands.tools.terminal.terminal.windows_terminal import (
        build_powershell_init_script,
    )

    script = build_powershell_init_script()

    assert CMD_OUTPUT_PS1_BEGIN.strip() not in script
    assert CMD_OUTPUT_PS1_END.strip() not in script
    # every statement is a complete line: no ">>" continuation over the pipe
    assert all(line.count("{") == line.count("}") for line in script.splitlines())


@pytest.mark.parametrize("command", MULTILINE_COMMANDS + SINGLE_LINE_COMMANDS)
def test_enter_false_appends_no_newline(command: str) -> None:
    """``enter=False`` must not submit anything, multiline or not."""
    sent = _sent_text(command, enter=False)

    assert not sent.endswith("\n")
