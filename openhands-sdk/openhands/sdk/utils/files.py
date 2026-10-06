import os
import re
import tempfile
from pathlib import Path


# ``atomic_write_text`` writes to ``.<name>.<8 random chars>`` (``mkstemp`` with
# that prefix; the random part uses ``[a-z0-9_]``) and then renames it to
# ``<name>``.
_ATOMIC_WRITE_TEMP_NAME = re.compile(r"\..+\.[a-z0-9_]{8}")


def is_atomic_write_temp_file(path: Path) -> bool:
    """Return True if ``path`` is named like an ``atomic_write_text`` temp file."""
    return _ATOMIC_WRITE_TEMP_NAME.fullmatch(path.name) is not None


def atomic_write_text(path: Path, value: str, mode: int = 0o600) -> None:
    """Atomically write text with owner-only permissions."""
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
