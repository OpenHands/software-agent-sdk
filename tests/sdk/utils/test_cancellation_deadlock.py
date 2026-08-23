"""Failing tests that demonstrate cancellation doesn't work on blocking sync code.

Two manifestations of the same underlying problem — a task blocked inside a
synchronous call (futex / blocking I/O / C-level lock) cannot be cancelled by
asyncio, because ``CancelledError`` can only be delivered at an ``await`` point.
The cleanup code knows this and *tries* to cancel, but gives up after a tiny
timeout, leaving zombie threads that accumulate until the process is killed.

Test 1 — ``AsyncExecutor.close()`` hangs forever on a task blocked in a worker
         thread (the bug addressed by PR #4548).

Test 2 — ``bubus`` EventBus handler timeout logs the error after the timeout
         but the handler's worker thread stays alive forever, because
         ``asyncio.wait_for`` cancels the coroutine but cannot interrupt the
         blocking sync call underneath.  The ``finally`` block only waits 0.1s
         for the cancellation to take effect, then abandons the task.

Both tests fail on ``main`` and should pass once the underlying cancellation
issue is fixed.
"""

import asyncio
import threading
import time

import pytest

from openhands.sdk.utils.async_executor import AsyncExecutor


# ── Test 1: AsyncExecutor.close() hangs forever ───────────────────────────


def test_async_executor_close_hangs_on_blocking_task():
    """AsyncExecutor.close() must not hang when a task is blocked in a worker
    thread that ignores cancellation.

    Without the fix (PR #4548): ``close()`` calls
    ``portal_cm.__exit__(None, None, None)`` which takes anyio's graceful path
    (``cancel_remaining=False``) and waits forever for the task to finish on
    its own.

    With the fix: ``close()`` cancels remaining tasks and bounds the thread
    join with a timeout, so it returns even when the task ignores cancellation.
    """
    import anyio
    from anyio.to_thread import run_sync

    thread_alive = threading.Event()

    async def blocked_in_worker_thread():
        thread_alive.set()
        # anyio cannot deliver cancellation until the worker thread returns.
        await run_sync(lambda: time.sleep(300))

    executor = AsyncExecutor()
    executor.portal.start_task_soon(blocked_in_worker_thread)
    time.sleep(0.2)  # let the task start

    done = threading.Event()

    def _close():
        try:
            # Try with timeout (available if PR #4548 is applied); fall back
            # to the unpatched close() signature.
            try:
                executor.close(timeout=2.0)
            except TypeError:
                executor.close()
        finally:
            done.set()

    threading.Thread(target=_close, daemon=True).start()

    # Without the fix, close() hangs forever — fail after 5s rather than hang.
    assert done.wait(timeout=5), (
        "AsyncExecutor.close() hung >5s on a task blocked in a worker thread. "
        "The task ignores cancellation (blocked in sync code), and close() "
        "does not bound the wait."
    )
    assert thread_alive.is_set(), "Task thread should have started"


# ── Test 2: bubus EventBus timeout leaves zombie threads ──────────────────


def test_bubus_timeout_does_not_free_blocked_handler_thread():
    """bubus EventBus handler timeout must actually free the thread, not just
    log a warning and abandon it.

    When a handler is blocked in synchronous code (e.g. a browser launch that
    hangs on a C-level lock), ``asyncio.wait_for`` cancels the asyncio task
    but cannot interrupt the underlying blocking call.  The ``finally`` block
    only waits 0.1s for the cancellation to take effect, then moves on —
    leaving the thread stuck forever.

    In production this accumulates zombie threads (one per browser launch
    failure) until the thread pool is exhausted and new conversation creation
    stalls.

    This test registers a handler that blocks in a worker thread, dispatches an
    event with a short timeout, and asserts that no zombie threads remain
    after the timeout fires and cleanup runs.
    """
    pytest.importorskip("bubus")
    from bubus import EventBus, BaseEvent

    thread_started = threading.Event()
    thread_should_stop = threading.Event()

    class TestEvent(BaseEvent):
        pass

    def _block_sync():
        # Simulate a handler blocked in synchronous C-level code (e.g. a
        # browser launch stuck on a C-level lock / futex inside playwright).
        # Unlike anyio.to_thread.run_sync, a raw ThreadPoolExecutor thread
        # cannot be cancelled by asyncio — CancelledError can only be
        # delivered at an await point, and run_in_executor doesn't cancel
        # the underlying thread.
        thread_started.set()
        thread_should_stop.wait(timeout=30)
        return "done"

    async def blocking_handler(event):
        # run_in_executor returns a Future; cancelling the asyncio task
        # cancels the Future's await but NOT the underlying thread.
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _block_sync)

    bus = EventBus(name="test_bus")
    bus.on(TestEvent, blocking_handler)
    bus._start()

    async def _run():
        # Dispatch the event — the handler will block for 30s
        bus.dispatch(TestEvent(event_timeout=1.0))
        # step() will time out after the event's timeout (1s)
        await bus.step(timeout=1.0)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=5))
    except (asyncio.TimeoutError, TimeoutError, Exception):
        pass  # Expected — the handler timed out

    time.sleep(1)  # give cleanup code time to run

    # Signal the thread to stop so it can exit if it's still alive
    thread_should_stop.set()
    time.sleep(0.5)

    bus.stop(timeout=1)

    # Count active non-daemon threads — if bubus properly cancelled the handler,
    # there should be no leftover threads from the blocking handler.
    active_worker_threads = [
        t for t in threading.enumerate()
        if t is not threading.main_thread() and t.is_alive() and not t.daemon
    ]

    # This assertion FAILS because bubus's timeout can't cancel blocking sync code:
    assert len(active_worker_threads) == 0, (
        f"bubus timeout fired but {len(active_worker_threads)} thread(s) are still "
        "alive. The handler was blocked in synchronous code and "
        "asyncio.wait_for could not deliver cancellation. The finally block "
        "waited only 0.1s then abandoned the task, leaving zombie threads "
        "that accumulate until the thread pool is exhausted."
    )
