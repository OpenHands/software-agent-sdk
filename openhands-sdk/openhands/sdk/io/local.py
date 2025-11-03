import os
import shutil

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import observe

from .base import FileStore


logger = get_logger(__name__)


class LocalFileStore(FileStore):
    root: str

    def __init__(self, root: str):
        if root.startswith("~"):
            root = os.path.expanduser(root)
        root = os.path.abspath(os.path.normpath(root))
        self.root = root
        os.makedirs(self.root, exist_ok=True)

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

    def read(self, path: str) -> str:
        full_path = self.get_full_path(path)
        with open(full_path, encoding="utf-8") as f:
            return f.read()

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
        try:
            full_path = self.get_full_path(path)
            if not os.path.exists(full_path):
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
