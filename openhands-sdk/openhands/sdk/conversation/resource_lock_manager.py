"""Resource-level lock manager for parallel tool execution.

Provides per-resource locking so that tools operating on the same shared state
(files, terminal session, browser session, …) are serialized while tools
touching *different* resources can run concurrently.

Locks are acquired in sorted order to prevent deadlocks and use FIFOLock
for fairness (no starvation).
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from openhands.sdk.conversation.fifo_lock import FIFOLock


DEFAULT_TIMEOUTS: Final[dict[str, float]] = {
    "file": 30.0,
    "terminal": 300.0,
    "browser": 300.0,
    "mcp": 300.0,
    "tool": 60.0,
}
_DEFAULT_TIMEOUT: Final[float] = 30.0


class ResourceLockTimeout(TimeoutError):
    """A lock could not be acquired within the allowed timeout."""


class ResourceLockManager:
    """Manages per-resource FIFO locks for concurrent tool execution.

    Usage::

        mgr = ResourceLockManager()
        with mgr.lock("file:/a.py", "file:/b.py"):
            # exclusive access to both files
            ...
    """

    def __init__(
        self,
        timeouts: dict[str, float] | None = None,
    ) -> None:
        self._locks: dict[str, FIFOLock] = {}
        self._meta_lock = threading.Lock()
        self._refcounts: dict[str, int] = {}
        self._timeouts = timeouts or DEFAULT_TIMEOUTS

    def _get_lock(self, key: str) -> FIFOLock:
        """Return (or lazily create) the FIFOLock for *key*.

        Also increments the reference count so the lock is not cleaned
        up while callers still hold or wait on it.
        """
        with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = FIFOLock()
            self._refcounts[key] = self._refcounts.get(key, 0) + 1
            return self._locks[key]

    def _release_lock(self, key: str) -> None:
        """Release the FIFOLock for *key* and clean up if unreferenced."""
        with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                return
            lock.release()
            self._refcounts[key] -= 1
            if self._refcounts[key] == 0 and not lock.locked():
                del self._locks[key]
                del self._refcounts[key]

    def _get_timeout(self, key: str) -> float:
        """Return the timeout for a resource key based on its prefix."""
        prefix = key.split(":", 1)[0] if ":" in key else key
        return self._timeouts.get(prefix, _DEFAULT_TIMEOUT)

    @contextmanager
    def lock(self, *resource_keys: str) -> Generator[None]:
        """Acquire locks for all *resource_keys* in sorted order.

        Sorted acquisition prevents deadlocks when two threads need
        overlapping sets of resources.

        Raises:
            ResourceLockTimeout: If a lock cannot be acquired within
                its timeout.
        """
        sorted_keys = sorted(set(resource_keys))
        acquired: list[str] = []
        try:
            for key in sorted_keys:
                timeout = self._get_timeout(key)
                if not self._get_lock(key).acquire(timeout=timeout):
                    # _get_lock() already incremented the refcount for this
                    # key. Since acquisition failed, this key won't be added
                    # to acquired[] and the finally block won't clean it up
                    # — so we must undo the refcount increment here.
                    with self._meta_lock:
                        self._refcounts[key] -= 1
                        if self._refcounts[key] == 0 and not self._locks[key].locked():
                            del self._locks[key]
                            del self._refcounts[key]
                    raise ResourceLockTimeout(
                        f"Could not acquire lock for '{key}' within {timeout}s"
                    )
                acquired.append(key)
            yield
        finally:
            for key in reversed(acquired):
                self._release_lock(key)
