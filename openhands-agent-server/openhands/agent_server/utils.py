import logging
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import PlainSerializer


logger = logging.getLogger(__name__)


def safe_rmtree(path: str | Path | None, description: str = "directory") -> bool:
    """Safely remove a directory tree, handling permission errors gracefully.

    Args:
        path: Path to the directory to remove
        description: Description of what's being removed (for logging)

    Returns:
        bool: True if removal was successful, False if it failed
    """
    if not path or not os.path.exists(path):
        return True

    def handle_remove_readonly(func, path, _exc):
        """Error handler for removing read-only files."""
        if os.path.exists(path):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except (OSError, PermissionError) as e:
                logger.warning(f"Failed to remove read-only file {path}: {e}")

    try:
        shutil.rmtree(path, onerror=handle_remove_readonly)
        logger.debug(f"Successfully removed {description}: {path}")
        return True
    except (OSError, PermissionError) as e:
        logger.warning(
            f"Failed to remove {description} at {path}: {e}. "
            f"This may leave temporary files on disk but won't affect functionality."
        )
        return False
    except Exception as e:
        logger.error(f"Unexpected error removing {description} at {path}: {e}")
        return False


def utc_now():
    """Return the current time in UTC format (Since datetime.utcnow is deprecated)"""
    return datetime.now(UTC)


def _uuid_to_hex(uuid_obj: UUID) -> str:
    """Converts a UUID object to a hex string without hyphens."""
    return uuid_obj.hex


OpenHandsUUID = Annotated[UUID, PlainSerializer(_uuid_to_hex, when_used="json")]
