"""Glob toolkit module.

- Exposes our Glob* classes lazily to avoid import-time side effects
- ALSO provides stdlib-compatible symbols (glob, iglob, escape)
  so that accidental shadowing of Python's `glob` module does not
  break third-party imports like `from glob import glob` during
  build-time tooling (e.g., setuptools discovery).
"""

from __future__ import annotations

import importlib.util as _il_util
import os as _os
import sysconfig as _sysconfig
from importlib import import_module as _import_module
from types import ModuleType as _ModuleType
from typing import Any


# Load stdlib glob module directly from the interpreter's stdlib path to avoid
# circular imports when this package shadows the top-level name "glob".
_stdlib_glob: _ModuleType | None = None
try:
    _stdlib_dir = _sysconfig.get_paths().get("stdlib")
    if _stdlib_dir:
        _glob_py = _os.path.join(_stdlib_dir, "glob.py")
        spec = _il_util.spec_from_file_location("_stdlib_glob", _glob_py)
        if spec and spec.loader:
            _mod = _il_util.module_from_spec(spec)
            spec.loader.exec_module(_mod)
            _stdlib_glob = _mod  # type: ignore[assignment]
except Exception:
    _stdlib_glob = None

if _stdlib_glob is None:
    # As a last resort, try a normal import. This may fail if circular, but in
    # most environments the stdlib resolution will succeed.
    import importlib as _importlib

    _stdlib_glob = _importlib.import_module("glob")

# Expose stdlib-compatible API so `from glob import glob` works during builds
glob = _stdlib_glob.glob  # type: ignore[attr-defined]
iglob = _stdlib_glob.iglob  # type: ignore[attr-defined]
escape = getattr(_stdlib_glob, "escape", lambda s: s)

__all__ = [
    # stdlib-compatible
    "glob",
    "iglob",
    "escape",
    # our exports (resolved lazily below)
    "GlobTool",
    "GlobAction",
    "GlobObservation",
    "GlobExecutor",
]


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    if name in {"GlobTool", "GlobAction", "GlobObservation"}:
        mod = _import_module("openhands.tools.glob.definition")
        return getattr(mod, name)
    if name == "GlobExecutor":
        mod = _import_module("openhands.tools.glob.impl")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
