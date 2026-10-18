"""Filesystem paths shared by plugin fetch implementations."""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.extensions.fetch import get_cache_path


PINNED_REFS_DIR = "resolved-refs"


def get_pinned_plugin_cache_path(
    source_url: str, cache_dir: Path, resolved_ref: str
) -> Path:
    """Return the immutable cache path for a plugin source at a commit SHA."""
    return get_cache_path(source_url, cache_dir) / PINNED_REFS_DIR / resolved_ref
