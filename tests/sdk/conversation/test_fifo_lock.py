"""
Test FIFO lock implementation for fairness and correctness.
"""

import threading
import time
from collections import deque

import pytest

from openhands.sdk.conversation.fifo_lock import FIFOLock


def test_fifo_lock_basic_functionality():
    """Test basic lock functionality - acquire, release, reentrancy."""
    lock = FIFOLock()

    # Test initial state
    assert not lock.locked()
    assert not lock.owned()

    # Test acquire/release
    lock.acquire()
    assert lock.locked()
    assert lock.owned()

    # Test reentrancy
    lock.acquire()
    assert lock.locked()
    assert lock.owned()

    lock.release()
    assert lock.locked()  # Still locked due to reentrancy
    assert lock.owned()

    lock.release()
    assert not lock.locked()
    assert not lock.owned()


def test_fifo_lock_context_manager():
    """Test context manager functionality."""
    lock = FIFOLock()

    with lock:
        assert lock.locked()
        assert lock.owned()

        # Test reentrancy with context manager
        with lock:
            assert lock.locked()
            assert lock.owned()

    assert not lock.locked()
    assert not lock.owned()


def test_fifo_lock_non_blocking():
    """Test non-blocking acquire behavior."""
    lock = FIFOLock()

    # Should acquire immediately when free
    assert lock.acquire(blocking=False)
    assert lock.locked()

    # Should fail when already owned by another thread
    def try_acquire():
        return lock.acquire(blocking=False)

    result = []
    thread = threading.Thread(target=lambda: result.append(try_acquire()))
    thread.start()
    thread.join()

    assert result[0] is False  # Should fail to acquire

    lock.release()
    assert not lock.locked()


def test_fifo_lock_timeout():
    """Test timeout behavior."""
    lock = FIFOLock()
    lock.acquire()

    def try_acquire_with_timeout():
        start_time = time.time()
        result = lock.acquire(blocking=True, timeout=0.1)
        end_time = time.time()
        return result, end_time - start_time

    result = []
    thread = threading.Thread(target=lambda: result.append(try_acquire_with_timeout()))
    thread.start()
    thread.join()

    acquired, duration = result[0]
    assert not acquired  # Should timeout
    assert 0.09 <= duration <= 0.2  # Should be close to timeout value

    lock.release()


def test_fifo_lock_fairness():
    """Test that lock provides FIFO ordering."""
    lock = FIFOLock()
    acquisition_order = deque()
    threads = []

    # Create individual events for each thread to ensure deterministic ordering
    thread_events = [threading.Event() for _ in range(10)]

    def worker(thread_id: int, my_event: threading.Event):
        # Wait for signal to proceed
        my_event.wait()
        with lock:
            acquisition_order.append(thread_id)
            time.sleep(0.001)  # Brief hold to ensure ordering is visible

    # Create threads in order
    for i in range(10):
        thread = threading.Thread(target=worker, args=(i, thread_events[i]))
        threads.append(thread)

    # Start all threads
    for thread in threads:
        thread.start()

    # Signal threads to proceed in exact order with small delays
    for i in range(10):
        thread_events[i].set()
        time.sleep(0.002)  # Small delay to ensure deterministic ordering

    # Wait for all to complete
    for thread in threads:
        thread.join()

    # Check that acquisition order matches creation order (FIFO)
    expected_order = list(range(10))
    actual_order = list(acquisition_order)

    assert actual_order == expected_order, (
        f"Expected FIFO order {expected_order}, got {actual_order}"
    )


def test_fifo_lock_error_handling():
    """Test error conditions."""
    lock = FIFOLock()

    # Should raise error when releasing unowned lock
    with pytest.raises(RuntimeError, match="Cannot release lock not owned"):
        lock.release()

    # Should raise error when releasing from wrong thread
    lock.acquire()

    def try_release():
        try:
            lock.release()
            return "success"
        except RuntimeError as e:
            return str(e)

    result = []
    thread = threading.Thread(target=lambda: result.append(try_release()))
    thread.start()
    thread.join()

    assert "Cannot release lock not owned" in result[0]

    lock.release()  # Clean up


def test_fifo_lock_stress_test():
    """Stress test with many threads to verify fairness under load."""
    lock = FIFOLock()
    acquisition_order = deque()
    num_threads = 20
    threads = []

    def worker(thread_id: int):
        # Randomized delay to create more realistic contention
        time.sleep(0.001 * (thread_id % 5))
        with lock:
            acquisition_order.append(thread_id)
            # Simulate some work
            time.sleep(0.001)

    # Create and start threads
    for i in range(num_threads):
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()

    # Wait for completion
    for thread in threads:
        thread.join()

    # Verify all threads acquired the lock
    assert len(acquisition_order) == num_threads

    # Verify no duplicates (each thread acquired exactly once)
    assert len(set(acquisition_order)) == num_threads

    # Note: We don't check exact FIFO order here due to timing variations,
    # but the main fairness test above verifies FIFO behavior


def run_fairness_test_multiple(num_runs: int = 100) -> list[bool]:
    """
    Run the fairness test multiple times sequentially to verify consistency.

    Args:
        num_runs: Number of sequential test runs

    Returns:
        List of boolean results (True = FIFO order maintained)
    """
    results = []

    def run_single_test():
        try:
            lock = FIFOLock()
            acquisition_order = deque()
            worker_threads = []

            # Use individual events to control each thread's acquire() call
            thread_events = [threading.Event() for _ in range(10)]

            def worker(thread_id: int):
                # Wait for this specific thread's signal
                thread_events[thread_id].wait()

                with lock:
                    acquisition_order.append(thread_id)
                    time.sleep(0.001)

            # Create worker threads
            for i in range(10):
                thread = threading.Thread(target=worker, args=(i,))
                worker_threads.append(thread)

            # Start all worker threads
            for thread in worker_threads:
                thread.start()

            # Give threads a moment to start and wait for their events
            time.sleep(0.01)

            # Signal threads to call acquire() in the exact order we want
            for i in range(10):
                thread_events[i].set()
                time.sleep(0.002)  # Small delay to ensure ordering

            # Wait for completion
            for thread in worker_threads:
                thread.join()

            # Check FIFO order
            expected = list(range(10))
            actual = list(acquisition_order)
            return actual == expected

        except Exception:
            return False

    # Run tests sequentially to avoid excessive thread contention
    for i in range(num_runs):
        if i % 20 == 0 and i > 0:
            print(f"  Completed {i}/{num_runs} tests...")
        result = run_single_test()
        results.append(result)

    return results


def test_release_all_drops_all_reentrant_levels_and_returns_depth():
    """release_all() fully frees a reentrantly-held lock and reports the depth."""
    lock = FIFOLock()
    lock.acquire()
    lock.acquire()
    lock.acquire()
    assert lock.owned()

    depth = lock.release_all()

    assert depth == 3
    assert not lock.locked()
    assert not lock.owned()


def test_release_all_requires_ownership():
    """release_all() from a non-owning thread raises, like release()."""
    lock = FIFOLock()

    def _grab_and_hold(started: threading.Event, release: threading.Event):
        lock.acquire()
        started.set()
        release.wait()
        lock.release()

    started, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_grab_and_hold, args=(started, release))
    holder.start()
    started.wait()
    try:
        with pytest.raises(RuntimeError):
            lock.release_all()
    finally:
        release.set()
        holder.join()


def test_reacquire_restores_reentrancy_depth():
    """reacquire(depth) restores the counter so releases balance out."""
    lock = FIFOLock()
    lock.acquire()
    lock.acquire()
    depth = lock.release_all()
    assert depth == 2

    lock.reacquire(depth)
    assert lock.owned()

    # Exactly `depth` releases return the lock to fully free.
    lock.release()
    assert lock.owned()  # still held (was reentrant)
    lock.release()
    assert not lock.owned()


def test_reacquire_zero_depth_is_noop():
    lock = FIFOLock()
    lock.reacquire(0)
    assert not lock.owned()


def test_release_all_then_reacquire_hands_off_to_waiter_first():
    """A thread waiting during the released window gets the lock before the
    releaser reacquires (FIFO fairness is preserved across the hand-off)."""
    lock = FIFOLock()
    lock.acquire()

    order: list[str] = []
    waiter_ready = threading.Event()

    def _waiter():
        waiter_ready.set()
        with lock:
            order.append("waiter")

    t = threading.Thread(target=_waiter)
    t.start()
    waiter_ready.wait()
    # Give the waiter a moment to actually enqueue on the lock.
    time.sleep(0.05)

    depth = lock.release_all()
    # Waiter should now be able to take it; reacquire queues behind it.
    lock.reacquire(depth)
    order.append("releaser")
    lock.release()
    t.join()

    assert order == ["waiter", "releaser"]


if __name__ == "__main__":
    print("Running FIFO lock fairness test 100 times sequentially...")

    results = run_fairness_test_multiple(100)

    success_count = sum(results)
    total_count = len(results)
    success_rate = success_count / total_count * 100

    print(f"Results: {success_count}/{total_count} tests maintained FIFO order")
    print(f"Success rate: {success_rate:.1f}%")

    if success_rate == 100.0:
        print("✅ FIFO lock provides perfect fairness!")
    elif success_rate >= 95.0:
        print("✅ FIFO lock provides excellent fairness (>95%)")
    elif success_rate >= 80.0:
        print("⚠️  FIFO lock provides good fairness (>80%)")
    else:
        print("❌ FIFO lock fairness is insufficient (<80%)")

    # Also run the regular tests
    print("\nRunning regular test suite...")
    pytest.main([__file__, "-v"])
