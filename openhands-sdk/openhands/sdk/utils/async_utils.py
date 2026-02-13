"""Async utilities for OpenHands SDK.

This module provides utilities for working with async callbacks in the context
of synchronous conversation handling.
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any

from openhands.sdk.event.base import Event


AsyncConversationCallback = Callable[[Event], Coroutine[Any, Any, None]]


class AsyncCallbackWrapper:
    """Wrapper that executes async callbacks in a different thread's event loop.

    This class implements the ConversationCallbackType interface (synchronous)
    but internally executes an async callback in an event loop running in a
    different thread. This allows async callbacks to be used in synchronous
    conversation contexts.

    Tracks pending futures to allow waiting for all callbacks to complete.
    """

    async_callback: AsyncConversationCallback
    loop: asyncio.AbstractEventLoop
    _pending_futures: list[Future]
    _lock: threading.Lock

    def __init__(
        self,
        async_callback: AsyncConversationCallback,
        loop: asyncio.AbstractEventLoop,
    ):
        self.async_callback = async_callback
        self.loop = loop
        self._pending_futures = []
        self._lock = threading.Lock()

    def __call__(self, event: Event):
        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.async_callback(event), self.loop
            )
            with self._lock:
                # Clean up completed futures to avoid unbounded memory growth
                self._pending_futures = [
                    f for f in self._pending_futures if not f.done()
                ]
                self._pending_futures.append(future)

    def wait_for_pending(self, timeout: float | None = None) -> None:
        """Wait for all pending callbacks to complete.

        Args:
            timeout: Maximum time to wait in seconds. None means wait indefinitely.

        Raises:
            TimeoutError: If timeout is exceeded while waiting.
        """
        with self._lock:
            futures = list(self._pending_futures)

        for future in futures:
            try:
                future.result(timeout=timeout)
            except Exception:
                # Exceptions in callbacks are already logged, ignore here
                pass
