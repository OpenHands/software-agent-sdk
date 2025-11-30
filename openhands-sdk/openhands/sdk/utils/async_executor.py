"""Reusable async-to-sync execution utility."""

import asyncio
import concurrent.futures
import inspect
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class AsyncExecutor:
    """
    Manages a background event loop for executing async code from sync contexts.

    This provides a robust async-to-sync bridge with proper resource management,
    timeout support, and thread safety.
    """

    _lock: threading.Lock

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._shutdown = threading.Event()

    def _safe_submit_on_loop(self, coro: Coroutine) -> concurrent.futures.Future:
        """Ensure the background event loop is running."""
        with self._lock:
            if self._shutdown.is_set():
                raise RuntimeError("AsyncExecutor has been shut down")

            if self._loop is not None:
                if self._loop.is_running():
                    return asyncio.run_coroutine_threadsafe(coro, self._loop)

                logger.warning(
                    "The loop is not empty, but it is not in a running state. "
                    "Under normal circumstances, this should not happen."
                )
                try:
                    self._loop.close()
                except RuntimeError as e:
                    logger.warning(f"Failed to close inactive loop: {e}")

            loop = asyncio.new_event_loop()

            def _runner():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_runner, daemon=True, name="AsyncExecutor")
            t.start()

            # Wait for loop to start
            while not loop.is_running():
                time.sleep(0.01)

            self._loop = loop
            self._thread = t
            return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _shutdown_loop(self) -> None:
        """Shutdown the background event loop."""
        if self._shutdown.is_set():
            logger.info("AsyncExecutor has been shut down")
            return

        with self._lock:
            if self._shutdown.is_set():
                return
            self._shutdown.set()
            loop, t = self._loop, self._thread
            self._loop = None
            self._thread = None

        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if t and t.is_alive():
            t.join(timeout=1.0)
            if t.is_alive():
                logger.warning("AsyncExecutor thread did not terminate gracefully")

        if loop and not loop.is_closed():
            try:
                if loop.is_running():
                    tasks = asyncio.all_tasks(loop)
                    for task in tasks:
                        if not task.done():
                            task.cancel()

                loop.close()
            except RuntimeError as e:
                logger.warning(f"Failed to close event loop: {e}")

    def run_async(
        self,
        awaitable_or_fn: Callable[..., Any] | Any,
        *args,
        timeout: float = 300.0,
        **kwargs,
    ) -> Any:
        """
        Run a coroutine or async function on the background loop from sync code.

        Args:
            awaitable_or_fn: Coroutine or async function to execute
            *args: Arguments to pass to the function
            timeout: Timeout in seconds (default: 300)
            **kwargs: Keyword arguments to pass to the function

        Returns:
            The result of the async operation

        Raises:
            TypeError: If awaitable_or_fn is not a coroutine or async function
            asyncio.TimeoutError: If the operation times out
        """
        if self._shutdown.is_set():
            raise RuntimeError("AsyncExecutor has been shut down")
        if inspect.iscoroutine(awaitable_or_fn):
            coro = awaitable_or_fn
        elif inspect.iscoroutinefunction(awaitable_or_fn):
            coro = awaitable_or_fn(*args, **kwargs)
        else:
            raise TypeError("run_async expects a coroutine or async function")

        fut = self._safe_submit_on_loop(coro)

        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
            raise
        except concurrent.futures.CancelledError:
            raise

    def close(self):
        """Close the async executor and cleanup resources."""
        self._shutdown_loop()

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass  # Ignore cleanup errors during deletion
