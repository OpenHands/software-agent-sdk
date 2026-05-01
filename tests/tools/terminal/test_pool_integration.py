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


def test_explicit_reset_recovers_after_tmux_server_exits(tmp_path, monkeypatch):
    """A killed tmux server should not make reset=True unusable."""
    tmux_tmpdir = tmp_path / "tmux"
    tmux_tmpdir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmpdir))

    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="tmux",
        max_panes=1,
    )
    try:
        timed_out = executor(
            TerminalAction(
                command="tmux -L openhands kill-server; sleep 10",
                timeout=1,
            )
        )
        assert timed_out.exit_code == -1
        assert timed_out.metadata is not None
        assert "timed out after 1" in timed_out.metadata.suffix

        recovered = executor(
            TerminalAction(command="echo recovered", reset=True, timeout=5)
        )
        assert recovered.exit_code == 0
        assert "Terminal session has been reset" in recovered.text
        assert "recovered" in recovered.text
    finally:
        executor.close()


def test_explicit_reset_does_not_interrupt_other_pooled_panes(
    tmp_path,
    monkeypatch,
):
    """A normal reset should replace one pane, not the whole pool."""
    tmux_tmpdir = tmp_path / "tmux-normal-reset"
    tmux_tmpdir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmpdir))

    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="tmux",
        max_panes=2,
    )
    errors: list[Exception] = []
    results: list[str] = []

    def run_long_command() -> None:
        try:
            obs = executor(
                TerminalAction(command="sleep 2 && echo still_running", timeout=5)
            )
            results.append(obs.text)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_long_command)
    thread.start()
    time.sleep(0.5)
    try:
        reset = executor(
            TerminalAction(command="echo reset_done", reset=True, timeout=5)
        )
        thread.join(timeout=6)

        assert reset.exit_code == 0
        assert "reset_done" in reset.text
        assert not thread.is_alive()
        assert not errors
        assert results == ["still_running"]
    finally:
        executor.close()
