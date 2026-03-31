"""Tests for ResourceLockManager."""

import threading

import pytest

from openhands.sdk.conversation.resource_lock_manager import (
    ResourceLockManager,
    ResourceLockTimeout,
)


def test_basic_lock_and_release():
    mgr = ResourceLockManager()
    with mgr.lock("file:/a.py"):
        pass  # should not raise


def test_no_keys_is_noop():
    mgr = ResourceLockManager()
    with mgr.lock():
        pass  # zero keys → no locks acquired, no error


def test_serializes_same_resource():
    """Two threads locking the same resource must not overlap."""
    mgr = ResourceLockManager()

    # Use events to prove strict serialization without sleeps
    inside = threading.Event()
    first_done = threading.Event()
    second_entered = threading.Event()
    violation = threading.Event()

    def first() -> None:
        with mgr.lock("file:/shared.py"):
            inside.set()
            # Wait until the second thread is *trying* to acquire
            # (give it a moment to reach the lock call)
            first_done.wait(timeout=5)

    def second() -> None:
        inside.wait(timeout=5)  # ensure first is inside
        with mgr.lock("file:/shared.py"):
            if not first_done.is_set():
                violation.set()  # would mean overlap
            second_entered.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()

    inside.wait(timeout=5)
    first_done.set()  # let first release
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert second_entered.is_set()
    assert not violation.is_set()


def test_parallel_different_resources():
    """Two threads locking different resources should overlap."""
    mgr = ResourceLockManager()
    barrier = threading.Barrier(2, timeout=5)
    reached_barrier = [False, False]

    def worker(idx: int, key: str) -> None:
        with mgr.lock(key):
            reached_barrier[idx] = True
            barrier.wait()  # both must reach here concurrently

    t1 = threading.Thread(target=worker, args=(0, "file:/a.py"))
    t2 = threading.Thread(target=worker, args=(1, "file:/b.py"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert all(reached_barrier)


def test_sorted_order_prevents_deadlock():
    """Sorted acquisition prevents deadlocks with opposite order."""
    mgr = ResourceLockManager()
    results: list[str] = []

    def worker(name: str, k1: str, k2: str) -> None:
        with mgr.lock(k1, k2):
            results.append(name)

    t1 = threading.Thread(target=worker, args=("A", "r:1", "r:2"))
    t2 = threading.Thread(target=worker, args=("B", "r:2", "r:1"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert set(results) == {"A", "B"}


def test_timeout_raises_custom_exception():
    mgr = ResourceLockManager(timeouts={"file": 0.05})

    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with mgr.lock("file:/x"):
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait()

    with pytest.raises(ResourceLockTimeout, match="file:/x"):
        with mgr.lock("file:/x"):
            pass

    release.set()
    t.join()


def test_timeout_is_subclass_of_timeout_error():
    """ResourceLockTimeout should be catchable as TimeoutError."""
    assert issubclass(ResourceLockTimeout, TimeoutError)


def test_duplicate_keys_deduplicated():
    """Passing the same key multiple times should not deadlock."""
    mgr = ResourceLockManager()
    with mgr.lock("file:/a.py", "file:/a.py"):
        pass


def test_default_timeouts():
    mgr = ResourceLockManager()
    assert mgr._get_timeout("file:/foo") == 30.0
    assert mgr._get_timeout("terminal:session") == 300.0
    assert mgr._get_timeout("browser:session") == 300.0
    assert mgr._get_timeout("mcp:server") == 300.0
    assert mgr._get_timeout("tool:my_tool") == 60.0
    assert mgr._get_timeout("unknown:key") == 30.0


def test_release_on_exception():
    """Lock must be released even if the body raises."""
    mgr = ResourceLockManager()
    with pytest.raises(RuntimeError):
        with mgr.lock("file:/a.py"):
            raise RuntimeError("boom")

    # Should be able to re-acquire immediately
    with mgr.lock("file:/a.py"):
        pass


def test_partial_release_on_timeout():
    """If the second lock times out, the first must be released."""
    mgr = ResourceLockManager(timeouts={"r": 0.05})

    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with mgr.lock("r:b"):
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait()

    with pytest.raises(ResourceLockTimeout):
        with mgr.lock("r:a", "r:b"):
            pass  # r:a acquired, r:b times out

    # r:a should have been released despite the timeout on r:b
    acquired = threading.Event()

    def check() -> None:
        with mgr.lock("r:a"):
            acquired.set()

    checker = threading.Thread(target=check)
    checker.start()
    checker.join(timeout=2)
    assert acquired.is_set()

    release.set()
    t.join()


def test_cleanup_removes_unused_locks():
    """After all holders release, the internal lock should be cleaned up."""
    mgr = ResourceLockManager()
    with mgr.lock("file:/tmp.py"):
        assert "file:/tmp.py" in mgr._locks

    # After release + cleanup, the lock entry should be gone
    assert "file:/tmp.py" not in mgr._locks


def test_cleanup_preserves_contended_locks():
    """A lock still waited on by another thread must not be cleaned up."""
    mgr = ResourceLockManager()
    held = threading.Event()
    second_waiting = threading.Event()
    release = threading.Event()

    def first() -> None:
        with mgr.lock("file:/x"):
            held.set()
            release.wait(timeout=5)
        # After first releases, cleanup runs — but second
        # is still referencing the lock, so it must survive.

    def second() -> None:
        held.wait(timeout=5)
        second_waiting.set()
        with mgr.lock("file:/x"):
            pass  # should succeed after first releases

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()

    held.wait(timeout=5)
    second_waiting.wait(timeout=5)
    # There is a small race here: second_waiting.set() fires before
    # _get_lock() increments the refcount. We cannot observe that
    # increment without test-only hooks in production code, so we
    # sleep briefly to make it overwhelmingly likely the second
    # thread has entered _get_lock() before we release the first.
    import time

    time.sleep(0.1)
    release.set()

    t1.join(timeout=5)
    t2.join(timeout=5)

    # Both completed without error — the lock was not prematurely deleted
    assert t1.is_alive() is False
    assert t2.is_alive() is False
