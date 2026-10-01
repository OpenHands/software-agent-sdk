import os
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from openhands.sdk.io.cache import MemoryLRUCache
from openhands.sdk.io.durability import DurabilityWriter
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.files import atomic_write_text
from openhands.sdk.utils.path import to_posix_path

from .base import FileStore


logger = get_logger(__name__)


class LocalFileStore(FileStore):
    root: str
    cache: MemoryLRUCache

    def __init__(
        self,
        root: str,
        cache_limit_size: int = 500,
        cache_memory_size: int = 20 * 1024 * 1024,
        *,
        deferred_durability: bool = True,
    ) -> None:
        """Initialize a LocalFileStore with caching.

        Args:
            root: Root directory for file storage.
            cache_limit_size: Maximum number of cached entries (default: 500).
            cache_memory_size: Maximum cache memory in bytes (default: 20MB).
            deferred_durability: When True (default), the fsync of each write
                runs on a per-store background DurabilityWriter instead of the
                calling thread (group commit). The write + atomic rename still
                happen synchronously, so content is immediately visible to
                readers and survives process exit. Queued fsync preserves the
                existing file-fsync guarantees, not directory durability.
                Call ``flush()`` at acknowledgment boundaries (end of a run)
                and ``close()`` on
                shutdown. When False, text writes fsync inline (previous
                behavior). Neither mode adds fsync to binary writes.

        Note:
            The cache assumes exclusive access to files. External modifications
            to files will not be detected and may result in stale cache reads.
        """
        if root.startswith("~"):
            root = os.path.expanduser(root)
        root = os.path.abspath(os.path.normpath(root))
        self.root = root
        os.makedirs(self.root, exist_ok=True)
        self.cache = MemoryLRUCache(cache_memory_size, cache_limit_size)
        self._deferred_durability = deferred_durability
        self._durability: DurabilityWriter | None = None
        self._durability_lock = threading.Lock()
        self._closed = False

    def get_full_path(self, path: str) -> str:
        # strip leading slash to keep relative under root
        if path.startswith("/"):
            path = path[1:]
        # normalize path separators to handle both Unix (/) and Windows (\) styles
        normalized_path = to_posix_path(path)
        full = os.path.abspath(
            os.path.normpath(os.path.join(self.root, normalized_path))
        )
        # ensure sandboxing
        if os.path.commonpath([self.root, full]) != self.root:
            raise ValueError(f"path escapes filestore root: {path}")

        return full

    def write(self, path: str, contents: str | bytes) -> None:
        # Keep admission, mutation and durability submission in one lifecycle
        # critical section. Close cannot seal a write between rename and enqueue.
        with self._durability_lock:
            if self._closed:
                raise RuntimeError("LocalFileStore is closed")
            full_path = self.get_full_path(path)
            writer = None
            if isinstance(contents, str) and self._deferred_durability:
                if self._durability is None:
                    self._durability = DurabilityWriter(
                        name=f"fs-durability-{id(self):x}"
                    )
                writer = self._durability
                writer.raise_if_failed()
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if isinstance(contents, str):
                if writer is not None:
                    atomic_write_text(
                        Path(full_path), contents, defer_fsync=writer.submit_fsync
                    )
                else:
                    atomic_write_text(Path(full_path), contents)
                self.cache[full_path] = contents
            else:
                with open(full_path, "wb") as f:
                    f.write(contents)
                # Don't cache binary content - LocalFileStore is meant for JSON data
                # If binary data is written and then read, it will error on read

    def flush(self) -> None:
        """Drain queued fsync work; re-raise the first durability failure."""
        with self._durability_lock:
            writer = self._durability
        if writer is not None:
            writer.flush()

    def close(self) -> None:
        """Reject new writes and drain admitted writes; reads remain available."""
        with self._durability_lock:
            self._closed = True
            writer = self._durability
            # Retain the writer so all close callers wait for its completion
            # and observe the same sticky durability failure.
        if writer is not None:
            writer.close()

    def read(self, path: str) -> str:
        full_path = self.get_full_path(path)

        if full_path in self.cache:
            return self.cache[full_path]

        if not os.path.exists(full_path):
            raise FileNotFoundError(path)

        with open(full_path, encoding="utf-8") as f:
            result = f.read()

        self.cache[full_path] = result
        return result

    def list(self, path: str) -> list[str]:
        full_path = self.get_full_path(path)
        if not os.path.exists(full_path):
            return []

        # If path is a file, return the file itself (S3-consistent behavior)
        if os.path.isfile(full_path):
            return [path]

        # Otherwise it's a directory, return its contents
        files = [os.path.join(path, f) for f in os.listdir(full_path)]
        files = [f + "/" if os.path.isdir(self.get_full_path(f)) else f for f in files]
        return files

    def delete(self, path: str) -> None:
        try:
            full_path = self.get_full_path(path)
            if not os.path.exists(full_path):
                logger.debug(f"Local path does not exist: {full_path}")
                return

            if os.path.isfile(full_path):
                os.remove(full_path)
                # Tolerate a cache miss: a file written by another process was
                # never cached here, and deleting it is not an error.
                self.cache.pop(full_path, None)
                logger.debug(f"Removed local file: {full_path}")
            elif os.path.isdir(full_path):
                shutil.rmtree(full_path)
                self.cache.clear()
                logger.debug(f"Removed local directory: {full_path}")

        except Exception as e:
            logger.error(f"Error clearing local file store: {str(e)}")

    def exists(self, path: str) -> bool:
        """Check if a file or directory exists."""
        return os.path.exists(self.get_full_path(path))

    def get_absolute_path(self, path: str) -> str:
        """Get absolute filesystem path."""
        return self.get_full_path(path)

    @contextmanager
    def lock(self, path: str, timeout: float = 30.0) -> Iterator[None]:
        """Acquire file-based lock using flock."""
        lock_path = self.get_full_path(path)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        file_lock = FileLock(lock_path)
        try:
            with file_lock.acquire(timeout=timeout):
                yield
        except Timeout:
            logger.error(f"Failed to acquire lock within {timeout}s: {lock_path}")
            raise TimeoutError(f"Lock acquisition timed out: {path}")
