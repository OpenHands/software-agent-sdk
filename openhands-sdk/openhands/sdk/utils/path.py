"""Path helpers for serialized and display-facing path strings."""

from __future__ import annotations

import logging
import os
import re
from functools import cache
from pathlib import Path, PureWindowsPath


logger = logging.getLogger(__name__)

_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

# Anchor for a relative OH_PERSISTENCE_DIR: captured once so the state tree
# cannot move when something chdirs mid-process.
_INITIAL_CWD = Path.cwd()


@cache
def _warn_relative_persistence_dir(env_dir: str) -> None:
    """Warn once per distinct relative value; this is called on every lookup."""
    logger.warning(
        "OH_PERSISTENCE_DIR=%r is relative; anchoring it to %s. Set an absolute path.",
        env_dir,
        _INITIAL_CWD,
    )


def get_user_persistence_dir(default: Path | None = None) -> Path:
    """Return the base directory for user-level OpenHands persistence.

    Honors the ``OH_PERSISTENCE_DIR`` environment variable when set (used by
    ephemeral/isolated sandboxes to redirect state onto a persistent volume),
    otherwise returns ``default``, or ``~/.openhands`` when no ``default`` is
    given. ``OH_PERSISTENCE_DIR`` replaces the ``~/.openhands`` base directory;
    callers append their usual subdirectories (e.g. ``profiles``,
    ``cache/skills``) to the result.

    ``OH_PERSISTENCE_DIR`` is expected to be absolute. A relative value is
    honored but anchored to the working directory this module was imported in,
    so a later ``chdir`` cannot split the state tree across call sites.

    Call sites that store the result in a module-level constant freeze it at
    import time, so ``OH_PERSISTENCE_DIR`` must be set before ``openhands.sdk``
    is imported; only call-time call sites pick up a later change.
    """
    env_dir = os.environ.get("OH_PERSISTENCE_DIR")
    if env_dir:
        resolved = Path(env_dir).expanduser()
        if not resolved.is_absolute():
            _warn_relative_persistence_dir(env_dir)
            resolved = _INITIAL_CWD / resolved
        return resolved
    if default is not None:
        return default
    return Path.home() / ".openhands"


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
