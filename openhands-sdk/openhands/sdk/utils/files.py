import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final


# Suffix that marks a file as still being written. ``atomic_write_text`` writes
# to ``.<name>.<random>.tmp`` next to ``path`` and then renames it to ``<name>``.
_TEMP_SUFFIX: Final[str] = ".tmp"


def is_temp_file(path: Path) -> bool:
    """Return True if ``path`` is named like a file that is still being written.

    That is any ``*.tmp`` name: ``atomic_write_text``'s temp files, and those of
    other writers that use the same suffix for a file they rename into place.
    """
    return path.suffix == _TEMP_SUFFIX


def atomic_write_text(
    path: Path,
    value: str,
    mode: int = 0o600,
    *,
    defer_fsync: Callable[[Path], None] | None = None,
) -> None:
    """Atomically write text with owner-only permissions.

    Args:
        path: Target file path.
        value: Text content to write.
        mode: File permission mode (default owner-only).
        defer_fsync: When provided, the fsync is NOT performed inline;
            instead ``defer_fsync(path)`` is invoked after the atomic rename
            so the caller can schedule durability off the current thread
            (group commit). The write + rename still happen synchronously,
            so the content is immediately visible to readers and survives
            process exit; power-loss durability is established when the
            deferred fsync completes. When None (default), fsync runs
            inline and the write is fully durable on return.
    """
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
            if defer_fsync is None:
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
    if defer_fsync is not None:
        defer_fsync(path)
