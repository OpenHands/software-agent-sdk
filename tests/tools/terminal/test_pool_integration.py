"""Integration tests verifying TerminalExecutor pool mode works end-to-end.

These tests exercise the full stack: TerminalExecutor → TmuxPanePool →
PooledTmuxTerminal, including declared_resources() and concurrent execution
through the executor's __call__ interface.
"""

import tempfile
import threading
import time

import pytest

from openhands.sdk.tool import DeclaredResources
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
    TerminalTool,
)
from openhands.tools.terminal.impl import TerminalExecutor


@pytest.fixture
def pool_executor():
    """Create a TerminalExecutor in pool mode."""
    with tempfile.TemporaryDirectory() as work_dir:
        executor = TerminalExecutor(
            working_dir=work_dir,
            terminal_type="tmux",
            max_panes=3,
        )
        yield executor
        executor.close()


class TestDeclaredResources:
    def test_pool_mode_opts_out_of_framework_locking(self, pool_executor):
        """In pool mode, declared_resources returns empty keys so the
        framework does not serialize terminal calls."""
        tool = TerminalTool(
            action_type=TerminalAction,
            observation_type=TerminalObservation,
            description="test",
            executor=pool_executor,
        )
        action = TerminalAction(command="echo hi")
        resources = tool.declared_resources(action)
        assert resources == DeclaredResources(keys=(), declared=True)

    def test_subprocess_mode_serializes(self):
        """In subprocess mode, declared_resources returns a resource key
        so the framework serializes terminal calls."""
        with tempfile.TemporaryDirectory() as work_dir:
            executor = TerminalExecutor(
                working_dir=work_dir,
                terminal_type="subprocess",
            )
            tool = TerminalTool(
                action_type=TerminalAction,
                observation_type=TerminalObservation,
                description="test",
                executor=executor,
            )
            action = TerminalAction(command="echo hi")
            resources = tool.declared_resources(action)
            assert resources == DeclaredResources(
                keys=("terminal:session",), declared=True
            )
            executor.close()


class TestConcurrentExecution:
    def test_parallel_calls_execute_concurrently(self, pool_executor):
        """Multiple concurrent executor calls run in parallel, not serially.

        Each call sleeps for 2s. With 3 panes, 3 calls should complete in
        well under 6s (serial) wall time.
        """
        num_calls = 3
        sleep_seconds = 2
        results: dict[int, str] = {}
        errors: list[Exception] = []

        def run(idx: int) -> None:
            try:
                action = TerminalAction(
                    command=f"sleep {sleep_seconds} && echo done", timeout=30
                )
                obs = pool_executor(action)
                results[idx] = obs.text
            except Exception as e:
                errors.append(e)

        start = time.monotonic()
        threads = [threading.Thread(target=run, args=(i,)) for i in range(num_calls)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.monotonic() - start

        assert not errors, f"Errors during parallel execution: {errors}"
        assert len(results) == num_calls
        for idx in range(num_calls):
            assert "done" in results[idx]
        # If calls were serial, elapsed would be >= 6s.
        # With parallelism it should be ~2s + overhead.
        serial_time = num_calls * sleep_seconds
        assert elapsed < serial_time, (
            f"Expected parallel execution under {serial_time}s, took {elapsed:.1f}s"
        )


class TestTmuxPoolRecovery:
    def test_shell_exit_returns_actionable_error_and_rebuilds_pool(self, pool_executor):
        obs = pool_executor(TerminalAction(command="exit 7", timeout=1.0))

        assert obs.is_error
        assert obs.exit_code == -1
        assert "rebuilt the terminal pool" in obs.text
        assert "top-level `exit`" in obs.text
        assert "Original tmux error:" in obs.text

        after = pool_executor(TerminalAction(command="echo after_rebuild", timeout=5.0))

        assert not after.is_error
        assert after.exit_code == 0
        assert "after_rebuild" in after.text

    def test_reset_after_shell_exit_uses_rebuilt_pool(self, pool_executor):
        obs = pool_executor(TerminalAction(command="exit 0", timeout=1.0))
        assert obs.is_error

        reset_obs = pool_executor(
            TerminalAction(command="pwd", reset=True, timeout=5.0)
        )

        assert not reset_obs.is_error
        assert reset_obs.exit_code == 0
        assert "Terminal session has been reset" in reset_obs.text
        assert pool_executor.working_dir in reset_obs.text


class TestPoolClose:
    def test_closing_the_executor_releases_a_call_waiting_on_a_command(
        self, pool_executor
    ):
        observations: list[TerminalObservation] = []
        action = TerminalAction(command="sleep 30", timeout=60)
        caller = threading.Thread(
            target=lambda: observations.append(pool_executor(action))
        )
        caller.start()
        time.sleep(2)

        closed_at = time.monotonic()
        pool_executor.close()
        caller.join(timeout=10)

        assert not caller.is_alive(), "the call kept waiting after close()"
        assert time.monotonic() - closed_at < 10
        (obs,) = observations
        assert obs.is_error
        assert "terminal was closed" in obs.text


class TestFollowUpsReachTheRunningCommand:
    """Follow-ups to a command that is still running must reach it (#6651).

    The observation for a no-change timeout tells the agent to answer with
    ``is_input``, wait with an empty command, or send ``C-c``. Checking out a
    pane for that follow-up must not interrupt the command first.
    """

    @pytest.fixture
    def executor(self):
        with tempfile.TemporaryDirectory() as work_dir:
            executor = TerminalExecutor(
                working_dir=work_dir,
                terminal_type="tmux",
                max_panes=3,
                no_change_timeout_seconds=2,
            )
            yield executor
            executor.close()

    def test_input_answers_a_waiting_prompt(self, executor):
        prompt = executor(
            TerminalAction(command="python3 -c \"print('hello ' + input('Name? '))\"")
        )
        assert prompt.exit_code == -1  # still waiting for input

        answer = executor(TerminalAction(command="bob", is_input=True))

        assert not answer.is_error, answer.text
        assert "hello bob" in answer.text

    def test_empty_command_and_ctrl_c_reach_a_long_command(self, executor):
        started = executor(
            TerminalAction(
                command="for i in 1 2 3; do echo tick$i; sleep 1; done; sleep 60"
            )
        )
        assert started.exit_code == -1

        more = executor(TerminalAction(command=""))
        assert not more.is_error, more.text

        stopped = executor(TerminalAction(command="C-c", is_input=True))
        assert not stopped.is_error, stopped.text
        assert stopped.exit_code == 130

    def test_new_command_after_a_finished_follow_up_runs_normally(self, executor):
        executor(TerminalAction(command="python3 -c \"input('x? ')\""))
        executor(TerminalAction(command="done", is_input=True))

        result = executor(TerminalAction(command="echo after"))

        assert not result.is_error, result.text
        assert "after" in result.text
        assert result.exit_code == 0
