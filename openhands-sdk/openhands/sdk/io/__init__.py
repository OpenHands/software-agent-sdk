from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .base import FileStore
    from .local import LocalFileStore
    from .memory import InMemoryFileStore


__all__ = ["LocalFileStore", "FileStore", "InMemoryFileStore"]

_LAZY_IMPORTS = {
    "LocalFileStore": (".local", "LocalFileStore"),
    "FileStore": (".base", "FileStore"),
    "InMemoryFileStore": (".memory", "InMemoryFileStore"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
