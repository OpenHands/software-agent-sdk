"""Tests for hard_timeout process reaping and error-channel reporting (#7017)."""

from __future__ import annotations

import time
from pathlib import Path

from openhands.sdk.llm import TextContent
from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor


def _llm_text(obs) -> str:
    return "\n".join(
        block.text for block in obs.to_llm_content if isinstance(block, TextContent)
    )


def test_hard_timeout_terminates_process_and_prevents_late_writes(tmp_path: Path):
    marker = tmp_path / "late_write.txt"
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
    )
    try:
        cmd = (
            'python3 -u -c "import time, pathlib; '
            "print('START_PROBE', flush=True); "
            "time.sleep(2.5); "
            f"pathlib.Path({str(marker)!r}).write_text('polluted')\""
        )
        obs = executor(TerminalAction(command=cmd, timeout=0.5, hard_timeout=True))

        assert obs.timeout is True
        assert obs.is_error is True
        assert obs.process_terminated is True
        assert "START_PROBE" in obs.text
        assert "timed out after 0.5 seconds and was terminated" in _llm_text(obs)

        # Wait past the 2.5s sleep to verify the background write never happens
        time.sleep(2.3)
        assert not marker.exists()

        # Subsequent commands in the same executor run cleanly
        follow = executor(TerminalAction(command="echo AFTER_HARD_TIMEOUT"))
        assert follow.is_error is False
        assert follow.exit_code == 0
        assert "AFTER_HARD_TIMEOUT" in follow.text
    finally:
        executor.close()


def test_hard_timeout_reaps_sigint_and_sigterm_ignoring_process(tmp_path: Path):
    marker = tmp_path / "sigint_ignored_write.txt"
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
    )
    try:
        setup = executor(TerminalAction(command="export SURVIVED_ENV=still_here"))
        assert setup.exit_code == 0

        cmd = (
            'python3 -u -c "import signal, time, pathlib; '
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('TRAPPED_READY', flush=True); "
            "time.sleep(3.0); "
            f"pathlib.Path({str(marker)!r}).write_text('escaped')\""
        )
        obs = executor(TerminalAction(command=cmd, timeout=0.5, hard_timeout=True))

        assert obs.timeout is True
        assert obs.is_error is True
        assert obs.process_terminated is True
        assert "TRAPPED_READY" in obs.text

        time.sleep(2.2)
        assert not marker.exists()

        # Shell stays alive with prior session state intact
        follow = executor(TerminalAction(command="echo $SURVIVED_ENV"))
        assert follow.exit_code == 0
        assert "still_here" in follow.text
    finally:
        executor.close()


def test_default_soft_timeout_preserves_existing_pause_behavior(tmp_path: Path):
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
    )
    try:
        soft_cmd = (
            'python3 -u -c "import time; '
            "print('SOFT_START', flush=True); time.sleep(2)\""
        )
        obs = executor(
            TerminalAction(
                command=soft_cmd,
                timeout=0.5,
            )
        )
        assert obs.timeout is True
        assert obs.is_error is False
        assert obs.process_terminated is False
        assert "SOFT_START" in obs.text
    finally:
        executor.close()
