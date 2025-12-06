import os
import shutil

from cachetools import LRUCache

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import observe

from .base import FileStore


logger = get_logger(__name__)


class LocalFileStore(FileStore):
    root: str
    cache: LRUCache

    def __init__(self, root: str, cache_size: int = 100) -> None:
        if root.startswith("~"):
            root = os.path.expanduser(root)
        root = os.path.abspath(os.path.normpath(root))
        self.root = root
        os.makedirs(self.root, exist_ok=True)
        self.cache = LRUCache(maxsize=cache_size)

    def get_full_path(self, path: str) -> str:
        # strip leading slash to keep relative under root
        if path.startswith("/"):
            path = path[1:]
        # normalize path separators to handle both Unix (/) and Windows (\) styles
        normalized_path = path.replace("\\", "/")
        full = os.path.abspath(
            os.path.normpath(os.path.join(self.root, normalized_path))
        )
        # ensure sandboxing
        if os.path.commonpath([self.root, full]) != self.root:
            raise ValueError(f"path escapes filestore root: {path}")

        return full

    @observe(name="LocalFileStore.write", span_type="TOOL")
    def write(self, path: str, contents: str | bytes) -> None:
        full_path = self.get_full_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        if isinstance(contents, str):
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(contents)
        else:
            with open(full_path, "wb") as f:
                f.write(contents)
        cache_content = (
            contents.decode("utf-8", errors="replace")
            if isinstance(contents, bytes)
            else contents
        )

        self.cache[full_path] = cache_content

    def read(self, path: str) -> str:
        full_path = self.get_full_path(path)

        if full_path in self.cache:
            return self.cache[full_path]

        if not os.path.exists(full_path):
            raise FileNotFoundError(path)
        result: str
        try:
            with open(full_path, encoding="utf-8") as f:
                result = f.read()
        except UnicodeDecodeError:
            logger.debug(f"File {full_path} is binary, reading as bytes")
            with open(full_path, "rb") as f:
                result = f.read().decode("utf-8", errors="replace")

        self.cache[full_path] = result
        return result

    @observe(name="LocalFileStore.list", span_type="TOOL")
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

    @observe(name="LocalFileStore.delete", span_type="TOOL")
    def delete(self, path: str) -> None:
        has_exist: bool = True
        full_path: str | None = None
        try:
            full_path = self.get_full_path(path)
            if not os.path.exists(full_path):
                has_exist = False
                logger.debug(f"Local path does not exist: {full_path}")
                return
            if os.path.isfile(full_path):
                os.remove(full_path)
                logger.debug(f"Removed local file: {full_path}")
            elif os.path.isdir(full_path):
                shutil.rmtree(full_path)
                logger.debug(f"Removed local directory: {full_path}")
        except Exception as e:
            logger.error(f"Error clearing local file store: {str(e)}")
        finally:
            if has_exist and full_path is not None:
                self._cache_delete(full_path)

    def _cache_delete(self, path: str) -> None:
        try:
            keys_to_delete = [key for key in self.cache.keys() if key.startswith(path)]
            for key in keys_to_delete:
                del self.cache[key]
            logger.debug(f"Cleared LRU cache: {path}")
        except Exception as e:
            logger.error(f"Error clearing LRU cache: {str(e)}")
