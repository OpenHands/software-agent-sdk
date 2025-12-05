from __future__ import annotations

import shutil

import pytest

from openhands.tools.terminal import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor


def _ensure_rg() -> str | None:
    return shutil.which("rg") or shutil.which("ripgrep")


@pytest.mark.skipif(_ensure_rg() is None, reason="ripgrep not available on PATH")
def test_terminal_with_hints_detects_rg(tmp_path):
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
        enable_command_hints=True,
    )
    try:
        obs = executor(TerminalAction(command="rg --version"))
        assert obs.parsed_tool == "terminal:rg"
        assert obs.parsed_argv and "rg" in obs.parsed_argv[0]
    finally:
        executor.close()


def test_terminal_without_hints_has_no_parsed_tool(tmp_path):
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
        enable_command_hints=False,
    )
    try:
        obs = executor(TerminalAction(command="echo hello"))
        assert obs.parsed_tool is None
        assert obs.parsed_argv is None
    finally:
        executor.close()
