# Compatibility shim: if this module is imported as top-level 'glob' during
# build isolation (e.g., by setuptools), delegate to the stdlib glob module.
# This avoids name shadowing from the package path 'openhands.tools.glob'.
import importlib.util
import os
import sys  # noqa: E401
import sysconfig


if __name__ == "glob":  # pragma: no cover - build-time path only
    stdlib = sysconfig.get_paths().get("stdlib")
    if stdlib:
        stdlib_glob_path = os.path.join(stdlib, "glob.py")
        spec = importlib.util.spec_from_file_location("glob", stdlib_glob_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules["glob"] = module
            globals().update(module.__dict__)

# Explicit imports so static type checkers (pyright) can resolve symbols.
# Guard these so that when this module is imported as top-level 'glob' during
# build isolation (e.g. setuptools import discovery), we don't attempt relative imports.
if __name__ != "glob":  # normal package import: openhands.tools.glob
    from .definition import GlobAction, GlobObservation, GlobTool  # noqa: E402
    from .impl import GlobExecutor  # noqa: E402

__all__ = [
    "GlobTool",
    "GlobAction",
    "GlobObservation",
    "GlobExecutor",
]
