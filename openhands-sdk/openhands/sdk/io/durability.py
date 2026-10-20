"""Ordered background durability ("group commit") for local file stores.

Motivation: a synchronous ``os.fsync`` on the caller's thread serializes
work that is otherwise concurrent. When the agent server runs many
conversations as coroutines on one event loop, every fsync in the
persistence path (event append, length-marker advance, base-state save)
blocks *all* conversations at once (issue #5402).

The scheme here keeps the write(2) + rename(2) on the calling thread —
cheap page-cache operations that make the data immediately visible to
readers and survive process exit — and moves only the fsync to a
process-wide bounded pool with a FIFO queue per store. Because each store's
work is strictly ordered, a
length-marker fsync never completes before the fsync of the event file it
acknowledges. This preserves file-fsync ordering, not power-loss recovery:
directory entries still follow the existing atomic-write implementation's
filesystem guarantees. The worker keeps draining after a failure so later
files still get their fsync attempt.

Acknowledgment boundaries: callers use :meth:`DurabilityWriter.flush` for
SDK message, fork and run completion and Agent Server startup/mutation
responses, and :meth:`DurabilityWriter.close` on store close. These drain
acknowledged writes before the operation returns or the store closes.
Intermediate state/event visibility is not a durability acknowledgment.
Failures are sticky: the first worker exception is
recorded and re-raised by ``raise_if_failed``/``flush``/``close`` so
durability errors propagate to callers instead of being swallowed.
"""

import os
import queue
import sys
import threading
from _thread import LockType
from collections.abc import Callable
from pathlib import Path

from openhands.sdk.logger import get_logger


if sys.platform == "win32":
    import _winapi
    import msvcrt


logger = get_logger(__name__)


class _DurabilityPool:
    """Bounded daemon workers; leaked stores must not hold up interpreter exit."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if len(self._threads) < 4:
                thread = threading.Thread(
                    target=self._worker,
                    name="openhands-durability-worker",
                    daemon=True,
                )
                try:
                    thread.start()
                except RuntimeError:
                    if not self._threads:
                        raise
                    # Existing workers can drain accepted stores even when
                    # the process cannot currently start another thread.
                else:
                    self._threads.append(thread)
            self._queue.put_nowait(fn)

    def _worker(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except BaseException:
                logger.exception("Durability store drainer failed")
            finally:
                self._queue.task_done()


_executor: _DurabilityPool | None = None
_executor_lock = threading.Lock()


def _reset_executor_after_fork() -> None:
    global _executor, _executor_lock
    _executor = None
    _executor_lock = threading.Lock()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=_reset_executor_after_fork)


def _get_executor() -> _DurabilityPool:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = _DurabilityPool()
        return _executor


def _open_fsync_file(path: Path) -> int:
    if sys.platform != "win32":
        return os.open(path, os.O_RDWR)
    # Share delete access so a later atomic write can replace this open file.
    handle = _winapi.CreateFile(
        str(path),
        _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
        0x7,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        0,
        _winapi.OPEN_EXISTING,
        0,
        0,
    )
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise


def fsync_file(path: Path) -> None:
    """fsync an already-written file, tolerating superseded/deleted paths.

    A queued fsync may run after its target was atomically replaced or
    deleted (e.g. a length marker superseded by a newer one). That is not
    an error: the replacement write queued its own fsync, and a deleted
    file needs no durability.
    """
    try:
        # Windows fsync (_commit/FlushFileBuffers) requires a writable handle.
        fd = _open_fsync_file(path)
    except FileNotFoundError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DurabilityError(Exception):
    """Raised when a deferred durability (fsync) task has failed."""


class DurabilityWriter:
    """Per-store FIFO execution on a bounded shared durability pool.

    One queue per store keeps per-conversation durability independent,
    with at most four stores executing concurrently and ordering within a
    conversation is preserved (event file fsync precedes the fsync of the
    length marker that acknowledges it).

    ``submit_fsync`` requires a shared ``write_lock`` held by producers across
    replacement and submission. Queued work pins descriptors only on workers;
    failed pool admission falls back to inline fsync before acknowledgment.
    Pending tasks and generation tokens use O(outstanding work) memory.
    """

    def __init__(
        self,
        name: str = "openhands-durability",
        *,
        write_lock: LockType | None = None,
    ) -> None:
        self._name = name
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._lock = threading.Lock()
        # Producers hold this across replacement + submission; the worker only
        # holds it while checking the generation and opening its file, not fsync.
        self._write_lock = write_lock if write_lock is not None else threading.Lock()
        self._has_write_lock = write_lock is not None
        self._pending_fsync: dict[Path, object] = {}
        self._thread: threading.Thread | None = None
        self._scheduled = False
        self._idle = threading.Event()
        self._idle.set()
        self._error: BaseException | None = None
        self._closed = False

    def _ensure_worker_locked(self) -> None:
        if not self._scheduled:
            self._scheduled = True
            self._idle.clear()
            try:
                _get_executor().submit(self._worker)
            except BaseException:
                self._scheduled = False
                self._idle.set()
                raise

    def _worker(self) -> None:
        thread = threading.current_thread()
        with self._lock:
            # A store can only be drained by one worker at a time.
            if self._thread is not None:
                return
            self._thread = thread
        previous_name = thread.name
        thread.name = self._name
        try:
            while True:
                with self._lock:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        self._thread = None
                        self._scheduled = False
                        self._idle.set()
                        return
                try:
                    try:
                        item()
                    except BaseException as e:
                        with self._lock:
                            if self._error is None:
                                self._error = e
                        logger.error("Deferred durability task failed: %s", e)
                finally:
                    self._queue.task_done()
        finally:
            thread.name = previous_name

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
            self._ensure_worker_locked()
            # Admission and enqueue remain indivisible with respect to close.
            self._queue.put(fn)

    def submit_fsync(self, path: Path) -> None:
        """Queue the current generation without retaining an open descriptor.

        Producers replacing a path must hold the shared ``write_lock`` across
        replacement and this call. Superseded tasks are skipped, so an older
        queue slot can never fsync a newer generation ahead of its dependencies.
        """
        if not self._has_write_lock:
            raise ValueError("submit_fsync requires a shared write_lock")
        with self._lock:
            if self._closed:
                raise RuntimeError("DurabilityWriter is closed")
            generation = object()
            try:
                try:
                    self._ensure_worker_locked()
                except RuntimeError:
                    # No work was admitted. Preserve inline durability rather
                    # than poisoning the store after transient pool admission.
                    fsync_file(path)
                    return
                self._pending_fsync[path] = generation
                self._queue.put(lambda: self._fsync_generation(path, generation))
            except BaseException as error:
                # The replacement is already visible. Never revive an older
                # token that could now fsync it in an earlier FIFO slot.
                self._pending_fsync.pop(path, None)
                if self._error is None:
                    self._error = error
                raise

    def _fsync_generation(self, path: Path, generation: object) -> None:
        with self._write_lock:
            with self._lock:
                if self._pending_fsync.get(path) is not generation:
                    return
                del self._pending_fsync[path]
                try:
                    # Pin on the worker while replacement is excluded, then
                    # release both locks before the potentially slow syscall.
                    fd = _open_fsync_file(path)
                except FileNotFoundError:
                    return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def flush(self) -> None:
        """Block until all queued work completes; re-raise the first failure."""
        self._queue.join()
        with self._lock:
            error = self._error
        if error is not None:
            raise DurabilityError("Deferred durability failed") from error

    def close(self) -> None:
        """Seal admission and drain this store's accepted work.

        Concurrent callers wait for the same drain, and repeated calls still
        report a recorded failure. Calling from a submitted task is unsupported
        because the worker cannot wait for its own completion.
        """
        with self._lock:
            if self._thread is threading.current_thread():
                raise RuntimeError("DurabilityWriter cannot close from its worker")
            self._closed = True
        self._idle.wait()
        self.flush()

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._error is not None
