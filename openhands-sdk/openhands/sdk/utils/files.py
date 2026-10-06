import os
import tempfile
from pathlib import Path


# Suffix that marks a file as still being written. ``atomic_write_text`` writes
# to ``.<name>.<random>.tmp`` next to ``path`` and then renames it to ``<name>``.
_TEMP_SUFFIX = ".tmp"


def is_temp_file(path: Path) -> bool:
    """Return True if ``path`` is named like a file that is still being written.

    That is any ``*.tmp`` name: ``atomic_write_text``'s temp files, and those of
    other writers that use the same suffix for a file they rename into place.
    """
    return path.suffix == _TEMP_SUFFIX


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
        os.replace(temporary_path, path)
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
