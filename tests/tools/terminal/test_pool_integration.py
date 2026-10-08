"""Integration tests verifying TerminalExecutor pool mode works end-to-end.

These tests exercise the full stack: TerminalExecutor → TmuxPanePool →
PooledTmuxTerminal, including declared_resources() and concurrent execution
through the executor's __call__ interface.
"""

import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import libtmux
import pytest
from libtmux.exc import LibTmuxException, TmuxObjectDoesNotExist

from openhands.sdk.tool import DeclaredResources
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
    TerminalTool,
)
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.terminal import create_terminal_session
from openhands.tools.terminal.terminal.terminal_session import TerminalSession
from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool


@pytest.fixture(params=[3])
def pool_executor(request):
    """Create a TerminalExecutor in pool mode."""
    with tempfile.TemporaryDirectory() as work_dir:
        executor = TerminalExecutor(
            working_dir=work_dir,
            terminal_type="tmux",
            max_panes=request.param,
        )
        yield executor
        executor.close()


def test_missing_session_recovers_while_server_survives(pool_executor, tmp_path):
    first = pool_executor(TerminalAction(command="echo ready", timeout=5))
    assert not first.is_error
    pool = pool_executor._pool
    assert pool is not None
    assert pool._server is not None
    assert pool._session is not None
    keeper = pool._server.new_session(session_name="keep-server-alive")
    marker = tmp_path / "not-replayed"
    try:
        pool._session.kill()
        obs = pool_executor(TerminalAction(command=f"touch {marker}", timeout=5))
        assert obs.is_error
        assert "rebuilt the terminal pool" in obs.text
        assert not marker.exists()
        after = pool_executor(TerminalAction(command="echo recovered", timeout=5))
        assert not after.is_error
        assert "recovered" in after.text
        assert not marker.exists()
    finally:
        keeper.kill()


@pytest.mark.parametrize(
    "error",
    [
        TmuxObjectDoesNotExist("Could not find object"),
        LibTmuxException(["server exited unexpectedly"]),
    ],
)
def test_missing_tmux_state_recovers_without_replaying_command(
    pool_executor, monkeypatch, tmp_path, error
):
    marker = tmp_path / "must-not-run"

    def missing_object(self, action):
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(TerminalSession, "execute", missing_object)
        obs = pool_executor(TerminalAction(command=f"touch {marker}", timeout=5))

    assert obs.is_error
    expected = (
        "replaced only the affected pane"
        if isinstance(error, TmuxObjectDoesNotExist)
        else "rebuilt the terminal pool"
    )
    assert expected in obs.text
    assert not marker.exists()
    after = pool_executor(TerminalAction(command="echo after_recovery", timeout=5))
    assert not after.is_error
    assert after.exit_code == 0
    assert "after_recovery" in after.text
    assert not marker.exists()


@pytest.mark.parametrize("missing_object", [False, True])
def test_pane_loss_does_not_interrupt_sibling(
    pool_executor, tmp_path, monkeypatch, missing_object
):
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    execute = TerminalSession.execute

    def execute_with_missing_object(session, action):
        if missing_object and action.command == "exit 1":
            raise TmuxObjectDoesNotExist("Could not find object")
        return execute(session, action)

    monkeypatch.setattr(TerminalSession, "execute", execute_with_missing_object)
    with ThreadPoolExecutor(max_workers=1) as workers:
        sibling = workers.submit(
            pool_executor,
            TerminalAction(
                command=(
                    f"touch {ready}; while [ ! -f {release} ]; do sleep 0.05; done; "
                    "printf 'SIBLING_%s\\n' DONE"
                ),
                timeout=15,
            ),
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            observation = pool_executor(TerminalAction(command="exit 1", timeout=5))
            assert observation.is_error
            assert "replaced only the affected pane" in observation.text
            assert "was not retried" in observation.text
        finally:
            release.touch()
        result = sibling.result(timeout=5)
        assert result.exit_code == 0
        assert "SIBLING_DONE" in result.text
    assert (
        pool_executor(TerminalAction(command="echo healthy", timeout=5)).exit_code == 0
    )


@pytest.mark.parametrize("pool_executor", [1], indirect=True)
def test_recovery_finishes_waiting_and_borrowed_calls(
    pool_executor, tmp_path, monkeypatch
):
    ready = tmp_path / "ready"
    borrowed = threading.Event()
    recovered = threading.Event()
    waiter_entered = threading.Event()
    prepare = TerminalExecutor._prepare_pooled_session
    recover = TerminalExecutor._recover_tmux_pool
    checkout = TmuxPanePool.checkout
    results = {}

    def pause_borrower(session):
        if threading.current_thread().name == "borrower":
            borrowed.set()
            assert recovered.wait(5)
        prepare(session)

    def enter_checkout(pool, timeout=None):
        if threading.current_thread().name == "waiter":
            waiter_entered.set()
        return checkout(pool, timeout)

    def recover_after_borrow(self, failed_pool):
        if threading.current_thread().name == "holder":
            assert borrowed.wait(5)
            assert waiter_entered.wait(5)
        try:
            recover(self, failed_pool)
        finally:
            recovered.set()

    def run(name, command):
        try:
            results[name] = pool_executor(TerminalAction(command=command, timeout=10))
        except Exception as error:
            results[name] = error

    monkeypatch.setattr(
        TerminalExecutor, "_prepare_pooled_session", staticmethod(pause_borrower)
    )
    monkeypatch.setattr(TerminalExecutor, "_recover_tmux_pool", recover_after_borrow)
    monkeypatch.setattr(TmuxPanePool, "checkout", enter_checkout)
    commands = {
        "holder": f"touch {ready}; sleep 1; tmux kill-server",
        "borrower": "echo borrower",
        "waiter": "echo waiter",
    }
    threads = {
        name: threading.Thread(target=run, args=(name, command), name=name, daemon=True)
        for name, command in commands.items()
    }
    threads["holder"].start()
    deadline = time.monotonic() + 5
    while not ready.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    threads["borrower"].start()
    assert borrowed.wait(5)
    threads["waiter"].start()
    try:
        for thread in threads.values():
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads.values())
        for name in commands:
            result = results[name]
            assert isinstance(result, TerminalObservation), result
            assert result.exit_code in (-1, 0)
        assert results["holder"].exit_code == -1
        assert results["borrower"].exit_code == -1
        after = pool_executor(TerminalAction(command="echo recovered", timeout=5))
        assert after.exit_code == 0
        assert "recovered" in after.text
    finally:
        recovered.set()
        pool_executor.close()


def test_unpooled_shell_exit_returns_observation(tmp_path):
    session = create_terminal_session(
        work_dir=str(tmp_path), terminal_type="tmux", no_change_timeout_seconds=1
    )
    session.initialize()
    try:
        observation = session.execute(TerminalAction(command="exit", timeout=2))
        assert observation.exit_code == -1
    finally:
        session.close()


@pytest.mark.parametrize("stderr", [[], ["can't find pane: %missing"]])
def test_capture_failure_returns_readable_recovery(
    pool_executor, monkeypatch, tmp_path, stderr
):
    pool_executor(TerminalAction(command="echo ready", timeout=5))
    cmd = libtmux.Pane.cmd
    failed = False

    def fail_once(pane, *args, **kwargs):
        nonlocal failed
        if args[0] == "capture-pane" and not failed:
            failed = True
            return SimpleNamespace(returncode=1, stderr=stderr, stdout=[])
        return cmd(pane, *args, **kwargs)

    monkeypatch.setattr(libtmux.Pane, "cmd", fail_once)
    marker = tmp_path / "not-replayed"
    observation = pool_executor(TerminalAction(command=f"touch {marker}", timeout=5))
    assert observation.is_error
    expected = "\n".join(stderr) or "capture-pane failed (rc=1)"
    assert observation.text.endswith(f"Original tmux error: {expected}")
    assert not marker.exists()
    assert (
        pool_executor(TerminalAction(command="echo healthy", timeout=5)).exit_code == 0
    )


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
