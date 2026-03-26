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


class TestTmuxPanePoolInit:
    def test_rejects_zero_panes(self):
        with pytest.raises(ValueError, match="max_panes must be >= 1"):
            TmuxPanePool(work_dir="/tmp", max_panes=0)

    def test_initialize_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = TmuxPanePool(work_dir=d, max_panes=1)
            p.initialize()
            p.initialize()  # should not raise
            p.close()


class TestCheckoutCheckin:
    def test_checkout_returns_terminal(self, pool):
        terminal = pool.checkout()
        assert terminal is not None
        assert terminal._initialized
        pool.checkin(terminal)

    def test_checkout_creates_panes_lazily(self, pool):
        assert pool.size == 0
        t1 = pool.checkout()
        assert pool.size == 1
        t2 = pool.checkout()
        assert pool.size == 2
        pool.checkin(t1)
        pool.checkin(t2)

    def test_checkin_reuses_panes(self, pool):
        t1 = pool.checkout()
        pool.checkin(t1)
        t2 = pool.checkout()
        assert t2 is t1
        pool.checkin(t2)

    def test_checkout_blocks_when_full(self, pool):
        # Check out all 3 panes
        panes = [pool.checkout() for _ in range(3)]
        assert pool.size == 3

        # Next checkout should time out
        with pytest.raises(TimeoutError):
            pool.checkout(timeout=0.2)

        for p in panes:
            pool.checkin(p)

    def test_checkout_unblocks_after_checkin(self, pool):
        panes = [pool.checkout() for _ in range(3)]
        result = [None]

        def delayed_checkin():
            time.sleep(0.1)
            pool.checkin(panes[0])

        t = threading.Thread(target=delayed_checkin)
        t.start()

        # Should succeed once the delayed checkin fires
        terminal = pool.checkout(timeout=2.0)
        result[0] = terminal
        t.join()

        assert result[0] is panes[0]
        pool.checkin(terminal)
        for p in panes[1:]:
            pool.checkin(p)


class TestContextManager:
    def test_pane_context_manager(self, pool):
        with pool.pane() as terminal:
            terminal.send_keys("echo hello")
            time.sleep(0.3)
            output = terminal.read_screen()
            assert "hello" in output
        # After exiting context, pane should be available again
        assert pool.available_count == 1

    def test_pane_context_manager_on_exception(self, pool):
        try:
            with pool.pane() as _terminal:
                raise RuntimeError("test error")
        except RuntimeError:
            pass
        # Pane should still be returned on exception
        assert pool.available_count == 1


class TestConcurrentExecution:
    def test_parallel_commands(self, pool):
        """Run commands on separate panes in parallel."""
        results = {}
        barrier = threading.Barrier(2)

        def run_cmd(label, cmd):
            with pool.pane() as terminal:
                barrier.wait(timeout=5)
                terminal.send_keys(cmd)
                time.sleep(0.5)
                results[label] = terminal.read_screen()

        t1 = threading.Thread(target=run_cmd, args=("a", "echo AAA"))
        t2 = threading.Thread(target=run_cmd, args=("b", "echo BBB"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert "AAA" in results["a"]
        assert "BBB" in results["b"]


class TestClose:
    def test_close_idempotent(self, pool):
        pool.close()
        pool.close()  # should not raise

    def test_checkout_after_close_raises(self, pool):
        pool.close()
        with pytest.raises(RuntimeError):
            pool.checkout()

    def test_checkin_foreign_pane_warns(self, pool, caplog):
        """Checkin of a pane not from this pool is ignored."""
        from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

        fake = TmuxTerminal.__new__(TmuxTerminal)
        pool.checkin(fake)  # should log warning, not crash


class TestIntrospection:
    def test_size_and_available(self, pool):
        assert pool.size == 0
        assert pool.available_count == 0

        t1 = pool.checkout()
        assert pool.size == 1
        assert pool.available_count == 0

        pool.checkin(t1)
        assert pool.size == 1
        assert pool.available_count == 1
