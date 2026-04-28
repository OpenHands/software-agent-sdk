"""Shared platform-sensitive test helpers."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest


def symlink_or_skip(source: Path, link_name: Path) -> None:
    """Create a symlink or skip when the environment lacks support."""
    try:
        link_name.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")


def supports_posix_execute_bits() -> bool:
    """Return whether the current environment has POSIX execute-bit semantics."""
    return os.name != "nt"


def can_fork_test_process() -> bool:
    """Return whether pytest-forked can safely isolate the current test."""
    return hasattr(os, "fork") and not os.environ.get("PYTEST_XDIST_WORKER")


def maybe_mark_forked[F: Callable[..., object]](test_func: F) -> F:
    """Apply pytest-forked only when the current worker can use it."""
    if can_fork_test_process():
        return pytest.mark.forked(test_func)
    return test_func


def set_address_space_limit_if_available(memory_limit: int) -> bool:
    """Apply an address-space limit when the platform exposes RLIMIT_AS."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    except Exception:
        return False
    return True
