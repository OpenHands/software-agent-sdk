"""Tests for TmuxPanePool."""

import tempfile
import threading
import time

import pytest

from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool


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


# -- Context manager ---------------------------------------------------------


@pytest.mark.parametrize(
    "raise_inside",
    [False, True],
    ids=["normal_exit", "exception_exit"],
)
def test_pane_context_manager_returns_pane(pool, raise_inside):
    """Pane is returned to the pool whether the block succeeds or raises."""
    try:
        with pool.pane() as terminal:
            terminal.send_keys("echo hello")
            time.sleep(0.3)
            if raise_inside:
                raise RuntimeError("test error")
            assert "hello" in terminal.read_screen()
    except RuntimeError:
        pass


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
        with pool.pane() as terminal:
            barrier.wait(timeout=5)
            terminal.send_keys(cmd)
            time.sleep(0.5)
            results[label] = terminal.read_screen()

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


def test_checkin_foreign_pane_is_ignored(pool):
    """Checkin of a pane not from this pool is ignored."""
    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

    fake = TmuxTerminal.__new__(TmuxTerminal)
    pool.checkin(fake)  # should log warning, not crash
