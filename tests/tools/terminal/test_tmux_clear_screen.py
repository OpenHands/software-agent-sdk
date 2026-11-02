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

from openhands.tools.terminal.constants import CMD_OUTPUT_PS1_END
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
    assert tmux_terminal.read_screen().rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())


def test_pane_usable_after_clear_screen(tmux_terminal):
    tmux_terminal.clear_screen()
    tmux_terminal.send_keys("echo after_clear_marker")
    time.sleep(0.3)
    assert "after_clear_marker" in tmux_terminal.read_screen()


def test_clear_screen_bounded_when_prompt_never_appears(tmux_terminal):
    """A pane that never shows a prompt must still return within the bound."""
    tmux_terminal.pane.send_keys("echo busy")  # leave something on screen
    original_visible_screen = tmux_terminal._visible_screen
    tmux_terminal._visible_screen = lambda: "no prompt here"  # type: ignore[method-assign]

    start = time.perf_counter()
    try:
        tmux_terminal.clear_screen()
    finally:
        tmux_terminal._visible_screen = original_visible_screen  # type: ignore[method-assign]

    elapsed = time.perf_counter() - start
    assert elapsed >= _CLEAR_PROMPT_TIMEOUT
    assert elapsed < _CLEAR_PROMPT_TIMEOUT + 0.5


def test_clear_screen_does_not_accept_the_previous_prompt(tmux_terminal):
    """The prompt already on screen before ``clear`` must not satisfy the wait.

    ``send_keys`` does not wait for the shell to run ``clear``, so right after
    sending it the pane still ends with the *previous* command's prompt. The wait
    must require the cleared shape (bare prompt, nothing above it), not just "ends
    with a prompt marker".
    """
    # Leave command output above the prompt, as a real completed command would.
    tmux_terminal.send_keys("echo PREVIOUS_OUTPUT_XYZ")
    time.sleep(0.3)
    previous_screen = tmux_terminal._visible_screen()
    assert previous_screen.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())

    # The pre-clear screen ends with a prompt but is not "cleared".
    assert tmux_terminal._screen_is_cleared() is False

    tmux_terminal.clear_screen()
    # After the wait the screen is genuinely cleared: nothing above the prompt.
    assert tmux_terminal._screen_is_cleared() is True
    assert "PREVIOUS_OUTPUT_XYZ" not in tmux_terminal.read_screen()


def test_clear_screen_empties_visible_screen_and_scrollback(tmux_terminal):
    """One ``clear_screen`` call clears both the screen and the scrollback."""
    tmux_terminal.send_keys("for i in $(seq 1 400); do echo SCROLL_$i; done")
    time.sleep(0.6)
    assert "SCROLL_1" in tmux_terminal.read_screen()

    tmux_terminal.clear_screen()

    # The visible rows are empty...
    assert tmux_terminal._screen_is_cleared() is True
    # ...and so is the scrollback that ``read_screen`` spans.
    assert "SCROLL_1" not in tmux_terminal.read_screen()


def test_clear_screen_does_not_queue_clear_on_a_clean_screen(tmux_terminal):
    """A clean screen must not get a queued ``clear``.

    ``send_keys`` returns before the shell runs the command, so if we queued a
    ``clear`` here the wait could accept the prompt already on screen and return
    with the ``clear`` still pending -- it would then run during the next command
    and wipe its completion marker. The clean path must drop the scrollback with
    a synchronous tmux command only, leaving nothing queued in the shell.
    """
    tmux_terminal.pane.cmd("send-keys", "clear", "Enter")
    time.sleep(0.3)
    assert tmux_terminal._screen_is_cleared() is True

    calls: list[tuple] = []
    original_cmd = tmux_terminal.pane.cmd

    def record_cmd(*args, **kwargs):
        calls.append(args)
        return original_cmd(*args, **kwargs)

    tmux_terminal.pane.cmd = record_cmd  # type: ignore[method-assign]
    try:
        tmux_terminal.clear_screen()
    finally:
        tmux_terminal.pane.cmd = original_cmd  # type: ignore[method-assign]

    assert not any("send-keys" in call for call in calls), (
        f"a clean screen must not queue `clear`, got {calls}"
    )
    assert any("clear-history" in call for call in calls)
