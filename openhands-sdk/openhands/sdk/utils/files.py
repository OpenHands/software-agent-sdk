import os
import tempfile
from collections.abc import Callable
from pathlib import Path


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
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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
