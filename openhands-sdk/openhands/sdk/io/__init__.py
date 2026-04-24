from __future__ import annotations

from typing import Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


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
