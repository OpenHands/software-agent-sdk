"""Shared platform-sensitive test helpers."""

from pathlib import Path

import pytest


def symlink_or_skip(source: Path, link_name: Path) -> None:
    """Create a symlink or skip when the environment lacks support."""
    try:
        link_name.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")
