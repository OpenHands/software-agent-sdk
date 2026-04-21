from .base import FileStore
from .local import LocalFileStore
from .memory import InMemoryFileStore

try:
    from .postgresql import PostgreSQLFileStore

    __all__ = ["LocalFileStore", "FileStore", "InMemoryFileStore", "PostgreSQLFileStore"]
except ImportError:
    __all__ = ["LocalFileStore", "FileStore", "InMemoryFileStore"]
