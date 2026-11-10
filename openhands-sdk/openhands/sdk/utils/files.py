import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final


# Suffix that marks a file as still being written. ``atomic_write_text`` writes
# to ``.<name>.<random>.tmp`` next to ``path`` and then renames it to ``<name>``.
_TEMP_SUFFIX: Final[str] = ".tmp"

# On Windows, a file being replaced cannot be opened by anyone else for an
# instant: ``os.replace`` holds the destination open with delete access, and
# Python opens files without delete sharing. So a replace fails while a reader
# has the destination open, and a read fails while a replace is in progress.
# Both raise ``PermissionError``, and both are retried every 10 ms for about
# 1 s before the error is raised. Readers hold the file only for one read, so
# under a steady stream of reads many short attempts get through where a few
# spaced-out ones can each land on a read. On other platforms neither fails
# that way, and neither is retried.
_RETRY_ON_PERMISSION_ERROR: Final[bool] = os.name == "nt"
_PERMISSION_ERROR_RETRY_DELAYS: Final[tuple[float, ...]] = (0.01,) * 100


def is_temp_file(path: Path) -> bool:
    """Return True if ``path`` is named like a file that is still being written.

    That is any ``*.tmp`` name: ``atomic_write_text``'s temp files, and those of
    other writers that use the same suffix for a file they rename into place.
    """
    return path.suffix == _TEMP_SUFFIX


def _retry_on_permission_error[T](operation: Callable[[], T]) -> T:
    """Run ``operation``, retried while a concurrent replace or read blocks it."""
    if _RETRY_ON_PERMISSION_ERROR:
        for delay in _PERMISSION_ERROR_RETRY_DELAYS:
            try:
                return operation()
            except PermissionError:
                time.sleep(delay)
    return operation()


def read_text_retrying(path: Path) -> str:
    """Read ``path`` as UTF-8, retried while a replace is in progress.

    For reading, without a lock, a file that ``atomic_write_text`` may replace
    at any moment.
    """
    return _retry_on_permission_error(lambda: path.read_text(encoding="utf-8"))


def atomic_write_text(path: Path, value: str, mode: int = 0o600) -> None:
    """Atomically write text with owner-only permissions."""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=_TEMP_SUFFIX, dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is None:
            os.chmod(temporary_path, mode)
        else:
            fchmod(fd, mode)
        file = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        _retry_on_permission_error(lambda: os.replace(temporary_path, path))
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
