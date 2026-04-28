"""Path helpers for serialized and display-facing path strings."""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath


_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def to_posix_path(path: str | os.PathLike[str]) -> str:
    """Return a slash-separated path string for wire/storage/display formats.

    This intentionally does not resolve or validate the path. Use ``Path`` or
    ``os.path`` directly when interacting with the local filesystem.
    """

    return os.fspath(path).replace("\\", "/")


def posix_path_name(path: str | os.PathLike[str]) -> str:
    """Return the final name from a slash-normalized path string."""

    normalized = to_posix_path(path).rstrip("/")
    return normalized.rsplit("/", 1)[-1] if normalized else ""


def is_local_path_source(source: str) -> bool:
    """Return whether a plugin/skill source is a local filesystem path.

    Backslashes identify local path syntax only when no URL scheme is present;
    they do not imply that a path is fully qualified. Windows drive-qualified
    paths are detected explicitly through ``PureWindowsPath``.
    """

    value = source.strip()
    if not value:
        return False
    if value in {".", ".."}:
        return True
    if value.startswith(("file://", "~", "/", "./", "../")):
        return True
    if Path(value).expanduser().is_absolute():
        return True
    if PureWindowsPath(value).is_absolute():
        return True
    return "\\" in value and _URL_SCHEME_RE.match(value) is None
