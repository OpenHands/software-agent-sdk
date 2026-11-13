"""Drain guard: repeated session probes and repeated interrupts.

Motivation: in real sessions, a model whose command gets stuck (or whose
output is slow) sometimes retries blindly: send ``C-c`` over and over, or
probe session health with trivial commands (``echo ok1``, ``echo ok2``, ...).
Each retry is a full agent turn — the outcome never changes, so the tokens
are pure drain. This guard short-circuits the pattern after the first
repeats with one instructive message: stop probing if the session is
healthy, or use ``reset=true`` if it is stuck. A real command (or a reset)
clears the streak, so legitimate interleaved use is unaffected.
"""

import pytest

from openhands.tools.terminal.definition import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import (
    _DRAIN_GUARD_LIMIT,
    TerminalExecutor,
)


@pytest.fixture
def executor_without_shell() -> TerminalExecutor:
    """Build a TerminalExecutor without touching the real shell.

    Same approach as ``test_literal_arg_guard.py``: bypass ``__init__``
    (which spins up a real tmux/subprocess session) and stub both
    shell-execution paths to raise. The drain guard runs *before* those
    paths, so a call that escapes the guard triggers the sentinel — that's
    how we prove the guard didn't fire.
    """
    exe = TerminalExecutor.__new__(TerminalExecutor)
    exe._pool = None
    exe._probe_streak = 0
    exe._cancel_streak = 0

    def _reach_shell(*_args: object, **_kwargs: object) -> TerminalObservation:
        raise AssertionError("Executor should not be consulted after the guard fired")

    exe._execute_pooled = _reach_shell  # type: ignore[method-assign]
    exe._execute_single_session = _reach_shell  # type: ignore[method-assign]
    return exe


class TestProbeGuard:
    """Repeated session-health probes are cut off after the limit."""

    def test_first_two_probes_reach_the_shell(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for i in range(_DRAIN_GUARD_LIMIT):
            action = TerminalAction(command=f"echo ok{i}")
            with pytest.raises(AssertionError):
                executor_without_shell(action)

    def test_probe_above_limit_returns_instructive_error(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for i in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command=f"echo ok{i}"))

        obs = executor_without_shell(TerminalAction(command="echo ok-more"))
        assert isinstance(obs, TerminalObservation)
        assert obs.is_error is True
        assert obs.exit_code is None
        assert obs.command == "echo ok-more"
        assert "Drain guard" in obs.text
        assert "reset=true" in obs.text

    @pytest.mark.parametrize(
        "command",
        ["echo ok", 'echo "ok"', "echo ok1", "printf ok2", "pwd", "Get-Date"],
    )
    def test_probe_variants_count_toward_streak(
        self, executor_without_shell: TerminalExecutor, command: str
    ) -> None:
        for i in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command=f"echo ok{i}"))
        obs = executor_without_shell(TerminalAction(command=command))
        assert "Drain guard" in obs.text

    @pytest.mark.parametrize(
        "command",
        [
            "echo done",  # different payload: not a session probe
            "echo ok > file.txt",  # redirect: real work, not a probe
            "git status",  # ordinary command
            "Get-ChildItem",  # ordinary PowerShell command
        ],
    )
    def test_non_probe_commands_pass_through(
        self, executor_without_shell: TerminalExecutor, command: str
    ) -> None:
        action = TerminalAction(command=command)
        with pytest.raises(AssertionError):
            executor_without_shell(action)


class TestStreakReset:
    """A real command (or reset) clears the streaks — interleaved use is fine."""

    def test_real_command_resets_probe_streak(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for i in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command=f"echo ok{i}"))
        # A real command passes the guard (reaches the shell stub)...
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="echo task-real"))
        # ...and clears the streak: the next two probes pass again.
        for i in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command=f"echo ok{i}"))

    def test_reset_clears_both_streaks(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for i in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command=f"echo ok{i}"))
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="", reset=True))
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="echo ok0"))

    def test_is_input_non_cancel_resets_cancel_streak(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        """Other keystrokes (e.g. typing into an interactive app) are not
        interrupts and must not accumulate toward the C-c limit."""
        for _ in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command="C-c", is_input=True))
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="q", is_input=True))
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="C-c", is_input=True))


class TestCancelGuard:
    """Repeated Ctrl-C interrupts are cut off after the limit."""

    def test_first_two_interrupts_reach_the_shell(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for _ in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command="C-c", is_input=True))

    def test_interrupt_above_limit_returns_instructive_error(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for _ in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command="C-c", is_input=True))

        obs = executor_without_shell(TerminalAction(command="C-c", is_input=True))
        assert isinstance(obs, TerminalObservation)
        assert obs.is_error is True
        assert obs.exit_code is None
        assert obs.command == "C-c"
        assert "Drain guard" in obs.text
        assert "reset=true" in obs.text

    def test_reset_command_clears_interrupt_streak(
        self, executor_without_shell: TerminalExecutor
    ) -> None:
        for _ in range(_DRAIN_GUARD_LIMIT):
            with pytest.raises(AssertionError):
                executor_without_shell(TerminalAction(command="C-c", is_input=True))
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="", reset=True))
        with pytest.raises(AssertionError):
            executor_without_shell(TerminalAction(command="C-c", is_input=True))
