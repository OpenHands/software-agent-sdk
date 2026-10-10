"""Tests for TmuxPanePool."""

import logging
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from libtmux.exc import LibTmuxException

from openhands.tools.terminal.constants import (
    TMUX_SESSION_HEIGHT,
    TMUX_SESSION_WIDTH,
)
from openhands.tools.terminal.terminal.tmux_pane_pool import (
    PooledTmuxTerminal,
    TmuxPanePool,
)
from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


def test_tmux_session_viewport_is_bounded():
    assert TMUX_SESSION_WIDTH <= 256
    assert TMUX_SESSION_HEIGHT <= 200


@pytest.fixture
def pool():
    """Create and initialize a pool, close it after the test."""
    with tempfile.TemporaryDirectory() as work_dir:
        p = TmuxPanePool(work_dir=work_dir, max_panes=3)
        p.initialize()
        yield p
        p.close()


# -- Init -------------------------------------------------------------------


@pytest.mark.parametrize("max_panes", [0, -1, -10])
def test_rejects_invalid_max_panes(max_panes):
    with pytest.raises(ValueError, match="max_panes must be >= 1"):
        TmuxPanePool(work_dir="/tmp", max_panes=max_panes)


def test_initialize_idempotent():
    with tempfile.TemporaryDirectory() as d:
        p = TmuxPanePool(work_dir=d, max_panes=1)
        p.initialize()
        p.initialize()  # should not raise
        p.close()


# -- Checkout / Checkin ------------------------------------------------------


def test_checkout_returns_initialized_terminal(pool):
    terminal = pool.checkout()
    assert terminal is not None
    assert terminal._initialized
    pool.checkin(terminal)


def test_checkout_creates_panes_lazily(pool):
    assert len(pool._all_panes) == 0
    t1 = pool.checkout()
    assert len(pool._all_panes) == 1
    t2 = pool.checkout()
    assert len(pool._all_panes) == 2
    pool.checkin(t1)
    pool.checkin(t2)


def test_checkin_reuses_panes(pool):
    t1 = pool.checkout()
    pool.checkin(t1)
    t2 = pool.checkout()
    assert t2 is t1
    pool.checkin(t2)


def test_checkout_blocks_when_full(pool):
    panes = [pool.checkout() for _ in range(3)]
    assert len(pool._all_panes) == 3

    with pytest.raises(TimeoutError):
        pool.checkout(timeout=0.2)

    for p in panes:
        pool.checkin(p)


def test_checkout_unblocks_after_checkin(pool):
    panes = [pool.checkout() for _ in range(3)]

    def delayed_checkin():
        time.sleep(0.1)
        pool.checkin(panes[0])

    t = threading.Thread(target=delayed_checkin)
    t.start()

    terminal = pool.checkout(timeout=2.0)
    t.join()

    assert terminal is panes[0]
    pool.checkin(terminal)
    for p in panes[1:]:
        pool.checkin(p)


@pytest.mark.parametrize("log_level", [logging.INFO, logging.DEBUG])
def test_pane_lifecycle_logging_survives_missing_session(pool, caplog, log_level):
    terminal = pool.checkout()
    pool.checkin(terminal)
    terminal.server.cmd("kill-server")
    caplog.set_level(
        log_level, logger="openhands.tools.terminal.terminal.tmux_pane_pool"
    )

    # Borrowing/returning a handle must not query tmux just to format a log.
    with pool.pane(timeout=0.2) as handle:
        assert handle.terminal is terminal
    with pool.pane(timeout=0.2) as handle:
        assert handle.terminal is terminal


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_checkout_initialization_failure_preserves_capacity(
    pool, monkeypatch, error_type
):
    assert pool._session is not None
    initial_windows = len(pool._session.windows)

    def fail_clear_screen(self):
        raise error_type("pane setup failed")

    with monkeypatch.context() as patch:
        patch.setattr(TmuxTerminal, "clear_screen", fail_clear_screen)
        for _ in range(pool.max_panes + 1):
            with pytest.raises(error_type, match="pane setup failed"):
                pool.checkout(timeout=0.2)
            assert len(pool._session.windows) == initial_windows

    panes = [pool.checkout(timeout=0.2) for _ in range(pool.max_panes)]
    for terminal in panes:
        pool.checkin(terminal)


def test_shell_death_during_setup_preserves_session(pool, monkeypatch):
    clear_screen = PooledTmuxTerminal.clear_screen

    def kill_during_setup(terminal):
        terminal.window.kill()
        clear_screen(terminal)

    with monkeypatch.context() as patch:
        patch.setattr(PooledTmuxTerminal, "clear_screen", kill_during_setup)
        with pytest.raises(LibTmuxException):
            pool.checkout(timeout=1)

    panes = [pool.checkout(timeout=1) for _ in range(pool.max_panes)]
    for terminal in panes:
        terminal.send_keys("printf 'SETUP_%s\\n' RECOVERED")
    time.sleep(0.3)
    for terminal in panes:
        assert "SETUP_RECOVERED" in terminal.read_screen()
        pool.checkin(terminal)


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("close", [False, True])
def test_slow_creation_does_not_block_pool_lifecycle(pool, monkeypatch, replace, close):
    warm = pool.checkout()
    old = pool.checkout() if replace else None
    entered = threading.Event()
    proceed = threading.Event()
    clear_screen = PooledTmuxTerminal.clear_screen

    def slow_setup(terminal):
        entered.set()
        assert proceed.wait(5)
        clear_screen(terminal)

    monkeypatch.setattr(PooledTmuxTerminal, "clear_screen", slow_setup)
    with ThreadPoolExecutor(max_workers=2) as workers:
        creating = (
            workers.submit(pool.replace, old)
            if replace
            else workers.submit(pool.checkout)
        )
        try:
            assert entered.wait(5)
            if close:
                workers.submit(pool.close).result(timeout=1)
                pool.checkin(warm)
            else:
                workers.submit(pool.checkin, warm).result(timeout=1)
                assert workers.submit(pool.checkout, 1).result(timeout=1) is warm
                pool.checkin(warm)
        finally:
            proceed.set()
        if close:
            with pytest.raises((RuntimeError, LibTmuxException)):
                creating.result(timeout=5)
            assert pool._server is not None
            assert not pool._server.cmd("list-windows", "-a").stdout
        else:
            pool.checkin(creating.result(timeout=5))
        if old is not None and close:
            pool.checkin(old)


# -- Replace -----------------------------------------------------------------


def test_replace_returns_new_terminal(pool):
    old = pool.checkout()
    new = pool.replace(old)
    assert new is not old
    assert new._initialized
    pool.checkin(new)


def test_replace_preserves_semaphore(pool):
    """Replace does not consume an extra semaphore slot."""
    t1 = pool.checkout()
    t2 = pool.checkout()
    t3 = pool.checkout()

    new_t1 = pool.replace(t1)

    with pytest.raises(TimeoutError):
        pool.checkout(timeout=0.2)

    pool.checkin(new_t1)
    pool.checkin(t2)
    pool.checkin(t3)


def test_replace_closes_old_pane(pool):
    old = pool.checkout()
    pool.replace(old)
    assert old._closed


def test_replace_does_not_affect_other_panes(pool):
    """Other checked-out panes keep working after a replace."""
    t1 = pool.checkout()
    t2 = pool.checkout()

    new_t1 = pool.replace(t1)
    t2.send_keys("echo still_alive")
    time.sleep(0.3)
    assert "still_alive" in t2.read_screen()

    pool.checkin(new_t1)
    pool.checkin(t2)


@pytest.mark.parametrize("cmd", ["echo fresh", "pwd"])
def test_replace_fresh_pane_runs_commands(pool, cmd):
    old = pool.checkout()
    new = pool.replace(old)
    new.send_keys(cmd)
    time.sleep(0.3)
    output = new.read_screen()
    assert output.strip()  # non-empty output
    pool.checkin(new)


# -- Concurrent execution ---------------------------------------------------


@pytest.mark.parametrize(
    "labels_and_cmds",
    [
        [("a", "echo AAA"), ("b", "echo BBB")],
        [("x", "echo X1"), ("y", "echo Y2"), ("z", "echo Z3")],
    ],
    ids=["two_threads", "three_threads"],
)
def test_parallel_commands(pool, labels_and_cmds):
    """Run commands on separate panes in parallel."""
    results = {}
    barrier = threading.Barrier(len(labels_and_cmds))

    def run_cmd(label, cmd):
        terminal = pool.checkout()
        try:
            barrier.wait(timeout=5)
            terminal.send_keys(cmd)
            time.sleep(0.5)
            results[label] = terminal.read_screen()
        finally:
            pool.checkin(terminal)

    threads = [
        threading.Thread(target=run_cmd, args=(label, cmd))
        for label, cmd in labels_and_cmds
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    for label, cmd in labels_and_cmds:
        expected = cmd.split()[-1]  # e.g. "AAA" from "echo AAA"
        assert expected in results[label]


def test_concurrent_replace_does_not_corrupt_pool(pool):
    """Replacing panes from multiple threads is safe."""
    errors = []

    def replace_cycle():
        try:
            t = pool.checkout(timeout=5)
            new = pool.replace(t)
            new.send_keys("echo ok")
            time.sleep(0.2)
            pool.checkin(new)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=replace_cycle) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Errors during concurrent replace: {errors}"


# -- Initial window cleanup -------------------------------------------------


def test_initial_window_killed_after_first_pane(pool):
    """The default tmux window is cleaned up on first checkout."""
    assert pool._initial_window is not None
    t = pool.checkout()
    assert pool._initial_window is None
    pool.checkin(t)


# -- Close -------------------------------------------------------------------


def test_close_idempotent(pool):
    pool.close()
    pool.close()  # should not raise


def test_checkout_after_close_raises(pool):
    pool.close()
    with pytest.raises(RuntimeError):
        pool.checkout()


def test_close_wakes_all_waiters_before_borrowers_return(pool):
    panes = [pool.checkout() for _ in range(pool.max_panes)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        waiting = [workers.submit(pool.checkout) for _ in range(4)]
        try:
            pool.close()
            for future in waiting:
                with pytest.raises(RuntimeError, match="closed"):
                    future.result(timeout=2)
        finally:
            for terminal in panes:
                pool.checkin(terminal)


def test_duplicate_checkin_does_not_add_capacity(pool):
    panes = [pool.checkout() for _ in range(pool.max_panes)]
    pool.checkin(panes[0])
    pool.checkin(panes[0])
    borrowed = pool.checkout(timeout=1)
    with pytest.raises(TimeoutError):
        pool.checkout(timeout=0.1)
    pool.checkin(borrowed)
    for terminal in panes[1:]:
        pool.checkin(terminal)


def test_checkin_foreign_pane_is_ignored(pool):
    """Checkin of a pane not from this pool is ignored."""
    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

    fake = TmuxTerminal.__new__(TmuxTerminal)
    pool.checkin(fake)  # should log warning, not crash


@pytest.mark.parametrize("pooled", [True, False])
@pytest.mark.parametrize("operation", ["read", "write", "close"])
def test_stale_terminal_cannot_access_restarted_server(
    tmp_path, monkeypatch, pooled, operation
):
    # Only redirect the socket namespace; all terminal/tmux operations are real.
    with tempfile.TemporaryDirectory(prefix="oh-tmux-") as socket_dir:
        monkeypatch.setenv("TMUX_TMPDIR", socket_dir)
        first = TmuxPanePool(str(tmp_path)) if pooled else TmuxTerminal(str(tmp_path))
        second = TmuxPanePool(str(tmp_path)) if pooled else TmuxTerminal(str(tmp_path))
        try:
            first.initialize()
            stale = first.checkout() if isinstance(first, TmuxPanePool) else first
            stale.server.cmd("kill-server")
            second.initialize()
            current = second.checkout() if isinstance(second, TmuxPanePool) else second
            current.send_keys("printf 'SECOND_%s\\n' CONVERSATION_MARKER")
            deadline = time.monotonic() + 5
            while "SECOND_CONVERSATION_MARKER" not in current.read_screen():
                assert time.monotonic() < deadline
                time.sleep(0.05)

            if operation == "read":
                if pooled:
                    with pytest.raises(LibTmuxException):
                        stale.read_screen()
                else:
                    assert stale.read_screen() == ""
            elif operation == "write":
                marker = tmp_path / "wrong-conversation"
                stale.send_keys(f"touch {marker}")
                current.send_keys("printf 'WRITE_%s\\n' BARRIER")
                deadline = time.monotonic() + 5
                while "WRITE_BARRIER" not in current.read_screen():
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                assert not marker.exists()
            else:
                first.close()
                assert "SECOND_CONVERSATION_MARKER" in current.read_screen()
        finally:
            first.close()
            second.close()
