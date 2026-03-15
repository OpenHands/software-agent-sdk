"""Async runner for interruptible LLM calls.

This module provides the AsyncRunner class which manages a background
event loop for running async LLM calls that can be cancelled from any thread.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import CancelledError as FutureCancelledError, Future
from typing import Any, TypeVar

from openhands.sdk.llm.exceptions import LLMCancelledError
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

T = TypeVar("T")


class AsyncRunner:
    """Manages async execution with cancellation support.

    This class manages a background event loop in a daemon thread, allowing
    synchronous callers to run async coroutines while supporting immediate
    cancellation via cancel().

    The event loop is created lazily on first use and can be cleaned up
    via close(). After close(), the runner can still be used - the event
    loop will be lazily recreated.

    Example:
        >>> runner = AsyncRunner(owner_id="my-llm")
        >>> result = runner.run(some_async_func(), "Call cancelled")
        >>> # From another thread:
        >>> runner.cancel()
    """

    def __init__(self, owner_id: str) -> None:
        """Initialize the async runner.

        Args:
            owner_id: Identifier for logging/debugging (e.g., LLM usage_id).
        """
        self._owner_id = owner_id
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._future: Future[Any] | None = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily create background event loop thread.

        The event loop runs in a daemon thread and is used to execute async
        coroutines. This allows synchronous callers to use async internally
        while supporting immediate cancellation.
        """
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                daemon=True,
                name=f"async-runner-{self._owner_id}",
            )
            self._thread.start()
            logger.debug(f"Started async runner thread for {self._owner_id}")
        return self._loop

    def run(self, coro: Coroutine[Any, Any, T], cancel_message: str) -> T:
        """Run an async coroutine with cancellation support.

        This method submits the coroutine to the background event loop and
        blocks until completion. The call can be cancelled via cancel().

        Args:
            coro: The coroutine to execute.
            cancel_message: Message for LLMCancelledError if cancelled.

        Returns:
            The result of the coroutine.

        Raises:
            LLMCancelledError: If cancelled via cancel().
        """
        loop = self._ensure_loop()
        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, loop)

        with self._lock:
            self._future = future

        try:
            return future.result()
        except (asyncio.CancelledError, FutureCancelledError):
            raise LLMCancelledError(cancel_message)
        finally:
            with self._lock:
                self._future = None

    def cancel(self) -> None:
        """Cancel any in-flight call (best effort).

        This method cancels the current call immediately. The cancellation
        takes effect at the next await point.

        Thread-safe: can be called from any thread. After cancellation,
        the runner can be used for new calls.
        """
        with self._lock:
            if self._future is not None:
                logger.info(f"Cancelling call for {self._owner_id}")
                self._future.cancel()

    def is_cancelled(self) -> bool:
        """Check if the current call has been cancelled.

        Returns:
            True if there's a current call and it has been cancelled.
        """
        with self._lock:
            if self._future is not None:
                return self._future.cancelled()
        return False

    def close(self) -> None:
        """Stop the background event loop and cleanup resources.

        This method should be called when the runner is no longer needed,
        especially in long-running applications to prevent thread leaks.

        After close(), the runner can still be used - the event loop
        will be lazily recreated on the next run() call.
        """
        # First, cancel any in-flight call (outside the lock to avoid
        # holding the lock during join)
        future_to_cancel: Future[Any] | None = None
        loop_to_stop: asyncio.AbstractEventLoop | None = None
        thread_to_join: threading.Thread | None = None

        with self._lock:
            future_to_cancel = self._future
            self._future = None
            loop_to_stop = self._loop
            self._loop = None
            thread_to_join = self._thread
            self._thread = None

        # Perform cleanup outside the lock
        if future_to_cancel is not None:
            future_to_cancel.cancel()

        if loop_to_stop is not None:
            loop_to_stop.call_soon_threadsafe(loop_to_stop.stop)

        if thread_to_join is not None:
            thread_to_join.join(timeout=2.0)
            if thread_to_join.is_alive():
                logger.warning(
                    f"Async runner thread for {self._owner_id} did not stop "
                    "within timeout"
                )
            else:
                logger.debug(f"Stopped async runner thread for {self._owner_id}")
