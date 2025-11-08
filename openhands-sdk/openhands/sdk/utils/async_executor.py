"""Reusable async-to-sync execution utility."""

import asyncio
import inspect
import threading
from collections.abc import Callable
from typing import Any


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

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure the background event loop is running."""
        with self._lock:
            if self._loop is not None:
                return self._loop

            loop = asyncio.new_event_loop()

            def _runner():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_runner, daemon=True, name="AsyncExecutor")
            t.start()

            # Wait for loop to start
            while not loop.is_running():
                pass

            self._loop = loop
            self._thread = t
            return loop

    def _shutdown_loop(self) -> None:
        """Shutdown the background event loop."""
        with self._lock:
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
        if inspect.iscoroutine(awaitable_or_fn):
            coro = awaitable_or_fn
        elif inspect.iscoroutinefunction(awaitable_or_fn):
            coro = awaitable_or_fn(*args, **kwargs)
        else:
            raise TypeError("run_async expects a coroutine or async function")

        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout)

    def close(self):
        """Close the async executor and cleanup resources."""
        self._shutdown_loop()

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass  # Ignore cleanup errors during deletion
