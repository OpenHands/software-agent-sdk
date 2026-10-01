"""Ordered background durability ("group commit") for local file stores.

Motivation: a synchronous ``os.fsync`` on the caller's thread serializes
work that is otherwise concurrent. When the agent server runs many
conversations as coroutines on one event loop, every fsync in the
persistence path (event append, length-marker advance, base-state save)
blocks *all* conversations at once (issue #5402).

The scheme here keeps the write(2) + rename(2) on the calling thread —
cheap page-cache operations that make the data immediately visible to
readers and survive process exit — and moves only the fsync to a
single-threaded, FIFO worker. Because the worker is strictly ordered, a
length-marker fsync never completes before the fsync of the event file it
acknowledges, so after an OS crash a durable marker can never claim more
events than are actually durable. The worker keeps draining after a
failure so later files still get their fsync attempt.

Acknowledgment boundaries: callers use :meth:`DurabilityWriter.flush` (at
the end of a run) and :meth:`DurabilityWriter.close` (on store close) to
guarantee acknowledged writes are durable before a conversation is
reported terminal. Failures are sticky: the first worker exception is
recorded and re-raised by ``raise_if_failed``/``flush``/``close`` so
durability errors propagate to callers instead of being swallowed.
"""

import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def fsync_file(path: Path) -> None:
    """fsync an already-written file, tolerating superseded/deleted paths.

    A queued fsync may run after its target was atomically replaced or
    deleted (e.g. a length marker superseded by a newer one). That is not
    an error: the replacement write queued its own fsync, and a deleted
    file needs no durability.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DurabilityError(Exception):
    """Raised when a deferred durability (fsync) task has failed."""


class DurabilityWriter:
    """Single-threaded FIFO executor for deferred fsync work.

    One writer per store keeps per-conversation durability independent:
    no cross-conversation serialization, and ordering within a
    conversation is preserved (event file fsync precedes the fsync of the
    length marker that acknowledges it).
    """

    def __init__(self, name: str = "openhands-durability") -> None:
        self._name = name
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._closed = False

    def _ensure_thread_locked(self) -> None:
        if self._thread is None:
            # Daemon so a leaked writer never blocks interpreter exit. Clean
            # exits still get durability: page-cache writes survive process
            # exit, and flush()/close() drain the queue before then.
            self._thread = threading.Thread(
                target=self._worker, name=self._name, daemon=True
            )
            self._thread.start()

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:  # shutdown sentinel
                    return
                try:
                    item()
                except BaseException as e:
                    with self._lock:
                        if self._error is None:
                            self._error = e
                    logger.error("Deferred durability task failed: %s", e)
            finally:
                self._queue.task_done()

    def raise_if_failed(self) -> None:
        """Fail-fast check for callers about to acknowledge a new write.

        Callers should invoke this *before* mutating on-disk state so a
        durability failure never surfaces after the mutation became
        visible. Also rejects use after :meth:`close`.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("DurabilityWriter is closed")
            error = self._error
        if error is not None:
            raise DurabilityError("Deferred durability failed") from error

    def submit(self, fn: Callable[[], None]) -> None:
        """Queue ``fn`` for ordered execution on the worker thread.

        Does not fail-fast on a recorded error (the worker keeps draining
        so later files still get fsync'ed); use :meth:`raise_if_failed` at
        acknowledgment points instead.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("DurabilityWriter is closed")
            self._ensure_thread_locked()
        self._queue.put(fn)

    def submit_fsync(self, path: Path) -> None:
        """Queue an fsync for ``path`` (already written + renamed)."""
        self.submit(lambda: fsync_file(path))

    def flush(self) -> None:
        """Block until all queued work completes; re-raise the first failure."""
        self._queue.join()
        with self._lock:
            error = self._error
        if error is not None:
            raise DurabilityError("Deferred durability failed") from error

    def close(self) -> None:
        """Flush, then stop the worker thread. Idempotent."""
        with self._lock:
            if self._closed:
                return
            thread = self._thread
        try:
            self.flush()
        finally:
            with self._lock:
                self._closed = True
            if thread is not None:
                self._queue.put(None)
                thread.join()

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._error is not None
