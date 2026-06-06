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
