"""Tests for AsyncExecutor shutdown behaviour."""

import threading
import time

import anyio
from anyio.to_thread import run_sync

from openhands.sdk.utils.async_executor import AsyncExecutor


def _close_in_background(executor: AsyncExecutor, **kwargs) -> threading.Event:
    """Call close() off-thread so a hang fails the test instead of freezing it."""
    done = threading.Event()

    def run() -> None:
        executor.close(**kwargs)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done


def test_close_returns_with_task_still_running():
    """close() must not wait for in-flight tasks to finish on their own."""
    executor = AsyncExecutor()
    executor.portal.start_task_soon(anyio.sleep_forever)
    time.sleep(0.1)

    done = _close_in_background(executor)

    assert done.wait(timeout=10), "close() blocked on a task that never finishes"


def test_close_gives_up_on_uncancellable_task():
    """A task that ignores cancellation must not block close() past the timeout."""

    async def blocked_in_worker_thread() -> None:
        # anyio cannot deliver cancellation until the worker thread returns.
        await run_sync(lambda: time.sleep(30))

    executor = AsyncExecutor()
    executor.portal.start_task_soon(blocked_in_worker_thread)
    time.sleep(0.1)

    done = _close_in_background(executor, timeout=1.0)

    assert done.wait(timeout=15), "close() blocked on an uncancellable task"


def test_close_is_idempotent():
    executor = AsyncExecutor()
    _ = executor.portal

    executor.close()
    executor.close()


def test_close_without_started_portal():
    """close() on a lazily-created executor that never started a portal."""
    AsyncExecutor().close()


def test_run_async_still_works_before_close():
    executor = AsyncExecutor()

    async def add(a: int, b: int) -> int:
        return a + b

    assert executor.run_async(add, 1, 2) == 3
    executor.close()
