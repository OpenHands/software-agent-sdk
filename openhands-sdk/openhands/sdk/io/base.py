from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager


class FileStore(ABC):
    """Abstract base class for file storage operations.

    This class defines the interface for file storage backends that can
    handle basic file operations like reading, writing, listing, and deleting files.

    Implementations should provide a locking mechanism via the `lock()` context
    manager for thread/process-safe operations.
    """

    @abstractmethod
    def write(self, path: str, contents: str | bytes) -> None:
        """Write contents to a file at the specified path.

        Args:
            path: The file path where contents should be written.
            contents: The data to write, either as string or bytes.
        """

    @abstractmethod
    def directWrite(self, path: str, contents: str | bytes) -> None:
        """Write contents directly to a file at the specified path (no caching).

        Args:
            path: The file path where contents should be written.
            contents: The data to write, either as string or bytes.
        """

    @abstractmethod
    def read(self, path: str) -> str:
        """Read and return the contents of a file as a string.

        Args:
            path: The file path to read from.

        Returns:
            The file contents as a string.
        """

    @abstractmethod
    def directRead(self, path: str) -> str:
        """Read and return file contents as a string directly (no caching).

        Args:
            path: The file path to read from.

        Returns:
            The file contents as a string.
        """

    @abstractmethod
    def list(self, path: str) -> list[str]:
        """List all files and directories at the specified path.

        Args:
            path: The directory path to list contents from.

        Returns:
            A list of file and directory names in the specified path.
        """

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete the file or directory at the specified path.

        Args:
            path: The file or directory path to delete.
        """

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file or directory exists at the specified path.

        Args:
            path: The file or directory path to check.

        Returns:
            True if the path exists, False otherwise.
        """

    @abstractmethod
    def get_absolute_path(self, path: str) -> str:
        """Get the absolute filesystem path for a given relative path.

        Args:
            path: The relative path within the file store.

        Returns:
            The absolute path on the filesystem.
        """

    @abstractmethod
    @contextmanager
    def lock(self, path: str, timeout: float = 30.0) -> Iterator[None]:
        """Acquire an exclusive lock for the given path.

        This context manager provides thread and process-safe locking.
        Implementations may use file-based locking, threading locks, or
        other mechanisms as appropriate.

        Args:
            path: The path to lock (used to identify the lock).
            timeout: Maximum seconds to wait for lock acquisition.

        Yields:
            None when lock is acquired.

        Raises:
            TimeoutError: If lock cannot be acquired within timeout.

        Note:
            File-based locking (flock) does NOT work reliably on NFS mounts
            or network filesystems.
        """
        yield  # pragma: no cover
