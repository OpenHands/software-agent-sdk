from __future__ import annotations

from importlib import import_module
from typing import Any


def import_lazy_symbol(
    module_name: str,
    module_globals: dict[str, Any],
    lazy_imports: dict[str, tuple[str, str]],
    name: str,
) -> Any:
    """Import and cache a lazily exported symbol."""
    try:
        import_path, attr_name = lazy_imports[name]
    except KeyError as exc:
        raise AttributeError(
            f"module {module_name!r} has no attribute {name!r}"
        ) from exc

    value = getattr(import_module(import_path, module_name), attr_name)
    module_globals[name] = value
    return value


def lazy_dir(module_globals: dict[str, Any], exports: list[str]) -> list[str]:
    """Return module attributes plus lazily exported symbols for ``dir()``."""
    return sorted(set(module_globals) | set(exports))
