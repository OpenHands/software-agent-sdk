"""Tests for TmuxTerminal.clear_screen prompt polling.

``clear_screen`` runs after every command (via ``_ready_for_next_command``), so
it must not block on a fixed sleep. It waits for the shell prompt instead,
bounded by ``_CLEAR_PROMPT_TIMEOUT``.
"""

import platform
import shutil
import tempfile
import time

import pytest

from openhands.tools.terminal.terminal.tmux_terminal import (
    _CLEAR_PROMPT_TIMEOUT,
    TmuxTerminal,
)


pytestmark = pytest.mark.skipif(
    platform.system() == "Windows" or shutil.which("tmux") is None,
    reason="tmux is required",
)


@pytest.fixture
def tmux_terminal():
    with tempfile.TemporaryDirectory() as tmpdir:
        term = TmuxTerminal(work_dir=tmpdir)
        term.initialize()
        yield term
        term.close()


def test_clear_screen_returns_once_prompt_is_visible(tmux_terminal):
    """The wait should end as soon as the prompt reappears, not after a fixed sleep."""
    # Warm up so the shell is idle at a prompt.
    tmux_terminal.send_keys("echo warmup")
    time.sleep(0.1)
    tmux_terminal.read_screen()

    start = time.perf_counter()
    tmux_terminal.clear_screen()
    elapsed = time.perf_counter() - start

    # A fixed 100 ms sleep would put every call at or above ~100 ms. The prompt
    # poll should return well under that on a healthy pane.
    assert elapsed < 0.1
    # And it must not return before the prompt is actually there.
    from openhands.tools.terminal.constants import CMD_OUTPUT_PS1_END

    assert tmux_terminal.read_screen().rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())


def test_pane_usable_after_clear_screen(tmux_terminal):
    tmux_terminal.clear_screen()
    tmux_terminal.send_keys("echo after_clear_marker")
    time.sleep(0.3)
    assert "after_clear_marker" in tmux_terminal.read_screen()


def test_clear_screen_bounded_when_prompt_never_appears(tmux_terminal):
    """A pane that never shows a prompt must still return within the bound."""
    tmux_terminal.pane.send_keys("echo busy")  # leave something on screen
    original_read_screen = tmux_terminal.read_screen
    tmux_terminal.read_screen = lambda: "no prompt here"  # type: ignore[method-assign]

    start = time.perf_counter()
    try:
        tmux_terminal.clear_screen()
    finally:
        tmux_terminal.read_screen = original_read_screen  # type: ignore[method-assign]

    elapsed = time.perf_counter() - start
    assert elapsed >= _CLEAR_PROMPT_TIMEOUT
    assert elapsed < _CLEAR_PROMPT_TIMEOUT + 0.5
