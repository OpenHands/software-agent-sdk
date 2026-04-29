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


def is_absolute_path_source(path: str | os.PathLike[str]) -> bool:
    """Return whether ``path`` is absolute in POSIX or Windows syntax."""

    value = os.fspath(path).strip()
    if not value:
        return False
    if value.startswith(("/", "\\")):
        return True
    if Path(value).expanduser().is_absolute():
        return True
    return PureWindowsPath(value).is_absolute()


def is_host_absolute_path(path: str | os.PathLike[str]) -> bool:
    """Return whether ``path`` is absolute for the current host filesystem."""

    value = os.fspath(path).strip()
    if not value:
        return False
    return Path(value).expanduser().is_absolute()


def is_local_path_source(source: str) -> bool:
    """Return whether a plugin/skill source should be treated as local.

    This accepts explicit local path syntax such as ``file://`` URLs,
    home-relative paths, any dot-prefixed relative path (``.``, ``..``,
    ``.openhands``), host-native absolute paths, Windows absolute paths, and
    backslash-separated paths when they are not URL-like.
    """

    value = source.strip()
    if not value:
        return False
    if value.startswith(("file://", "~", ".")):
        return True
    if is_absolute_path_source(value):
        return True
    return "\\" in value and _URL_SCHEME_RE.match(value) is None
