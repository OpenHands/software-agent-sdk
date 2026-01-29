import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from regex import P

from openhands.sdk.io.base import FileStore
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class InMemoryFileStore(FileStore):
    files: dict[str, str]
    _instance_id: str
    _lock: threading.Lock

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = {}
        self._instance_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        if files is not None:
            self.files = files

    def write(self, path: str, contents: str | bytes) -> None:
        if isinstance(contents, bytes):
            contents = contents.decode("utf-8")
        self.files[path] = contents
    
    def directWrite(self, path, contents):
        return self.write(path, contents)

    def read(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]
    
    def directRead(self, path):
        return self.read(path)

    def list(self, path: str) -> list[str]:
        files = []
        for file in self.files:
            if not file.startswith(path):
                continue
            suffix = file.removeprefix(path)
            parts = suffix.split("/")
            if parts[0] == "":
                parts.pop(0)
            if len(parts) == 1:
                files.append(file)
            else:
                dir_path = os.path.join(path, parts[0])
                if not dir_path.endswith("/"):
                    dir_path += "/"
                if dir_path not in files:
                    files.append(dir_path)
        return files

    def delete(self, path: str) -> None:
        try:
            keys_to_delete = [key for key in self.files.keys() if key.startswith(path)]
            for key in keys_to_delete:
                del self.files[key]
            logger.debug(f"Cleared in-memory file store: {path}")
        except Exception as e:
            logger.error(f"Error clearing in-memory file store: {e}")

    def exists(self, path: str) -> bool:
        """Check if a file exists."""
        if path in self.files:
            return True
        return any(f.startswith(path + "/") for f in self.files)

    def get_absolute_path(self, path: str) -> str:
        """Get absolute path (uses temp dir with unique instance ID)."""
        import tempfile

        return os.path.join(
            tempfile.gettempdir(), f"openhands_inmemory_{self._instance_id}", path
        )

    @contextmanager
    def lock(self, path: str, timeout: float = 30.0) -> Iterator[None]:
        """Acquire thread lock for in-memory store."""
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(f"Lock acquisition timed out: {path}")
        try:
            yield
        finally:
            self._lock.release()
