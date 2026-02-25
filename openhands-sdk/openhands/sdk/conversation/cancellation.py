"""Cancellation token for cooperative cancellation of operations.

This module provides a thread-safe mechanism for cancelling long-running operations
like LLM completions and tool executions. The pattern is inspired by Rust's
tokio_util::sync::CancellationToken and .NET's CancellationToken.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class CancellationError(Exception):
    """Raised when an operation is cancelled via CancellationToken."""

    pass


class CancellationToken:
    """Thread-safe cancellation token for cooperative cancellation.

    This token can be used to signal cancellation across threads and register
    callbacks that are invoked when cancellation occurs.

    Example:
        >>> token = CancellationToken()
        >>> token.register_callback(lambda: print("Cancelled!"))
        >>> # In another thread:
        >>> token.cancel()  # Prints "Cancelled!"

    The token is designed to work with:
    - LLM completions (close HTTP client)
    - Terminal commands (send SIGINT)
    - Any long-running operation that can check is_cancelled()
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Cancel the token and invoke all registered callbacks.

        This method is thread-safe and idempotent - calling it multiple times
        has no additional effect after the first call.

        Callbacks are invoked synchronously in the order they were registered.
        Exceptions in callbacks are logged but do not prevent other callbacks
        from being invoked.
        """
        if self._cancelled.is_set():
            return

        self._cancelled.set()
        logger.debug("CancellationToken cancelled, invoking callbacks")

        with self._lock:
            callbacks = self._callbacks.copy()

        for callback in callbacks:
            try:
                callback()
            except Exception as e:
                logger.warning(f"Exception in cancellation callback: {e}")

    def is_cancelled(self) -> bool:
        """Check if this token has been cancelled.

        Returns:
            True if cancel() has been called, False otherwise.
        """
        return self._cancelled.is_set()

    def throw_if_cancelled(self) -> None:
        """Raise CancellationError if this token has been cancelled.

        This is a convenience method for checking cancellation at safe points
        in your code.

        Raises:
            CancellationError: If the token has been cancelled.
        """
        if self._cancelled.is_set():
            raise CancellationError("Operation was cancelled")

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked when cancellation occurs.

        If the token is already cancelled, the callback is invoked immediately.

        Args:
            callback: A no-argument callable to invoke on cancellation.
        """
        with self._lock:
            self._callbacks.append(callback)

        # If already cancelled, invoke immediately
        if self._cancelled.is_set():
            try:
                callback()
            except Exception as e:
                logger.warning(f"Exception in cancellation callback: {e}")

    def unregister_callback(self, callback: Callable[[], None]) -> bool:
        """Remove a previously registered callback.

        Args:
            callback: The callback to remove.

        Returns:
            True if the callback was found and removed, False otherwise.
        """
        with self._lock:
            try:
                self._callbacks.remove(callback)
                return True
            except ValueError:
                return False

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the token to be cancelled.

        Args:
            timeout: Maximum seconds to wait, or None to wait indefinitely.

        Returns:
            True if the token was cancelled, False if timeout expired.
        """
        return self._cancelled.wait(timeout=timeout)

    def child_token(self) -> CancellationToken:
        """Create a child token that is cancelled when this token is cancelled.

        The child token can also be cancelled independently without affecting
        the parent.

        Returns:
            A new CancellationToken linked to this one.
        """
        child = CancellationToken()
        self.register_callback(child.cancel)
        return child
