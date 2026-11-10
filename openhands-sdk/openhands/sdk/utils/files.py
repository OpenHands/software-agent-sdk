import os
import tempfile
import time
from pathlib import Path
from typing import Final


# Suffix that marks a file as still being written. ``atomic_write_text`` writes
# to ``.<name>.<random>.tmp`` next to ``path`` and then renames it to ``<name>``.
_TEMP_SUFFIX: Final[str] = ".tmp"

# On Windows, ``os.replace`` raises ``PermissionError`` while another handle
# has the destination open, because Python opens files without delete
# sharing. Readers hold the file only while reading it, so the replace is
# retried after these delays (about 0.9 s in total) before the error is raised.
# On other platforms a replace never fails that way, and it is not retried.
_RETRY_REPLACE_ON_PERMISSION_ERROR: Final[bool] = os.name == "nt"
_REPLACE_RETRY_DELAYS: Final[tuple[float, ...]] = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5)


def is_temp_file(path: Path) -> bool:
    """Return True if ``path`` is named like a file that is still being written.

    That is any ``*.tmp`` name: ``atomic_write_text``'s temp files, and those of
    other writers that use the same suffix for a file they rename into place.
    """
    return path.suffix == _TEMP_SUFFIX


def _replace(source: Path, destination: Path) -> None:
    """``os.replace``, retried while a reader has ``destination`` open."""
    if _RETRY_REPLACE_ON_PERMISSION_ERROR:
        for delay in _REPLACE_RETRY_DELAYS:
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                time.sleep(delay)
    os.replace(source, destination)


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
        _replace(temporary_path, path)
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
