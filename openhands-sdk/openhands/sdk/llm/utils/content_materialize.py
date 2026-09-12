"""Helpers for materializing multipart content to a workspace filesystem.

These functions decode ``data:`` URLs or download ``http(s)://`` URLs (with a
size cap) and write the bytes into the workspace using a deterministic,
content-addressed path. Content-addressing makes writes idempotent: the same
payload always maps to the same file, so replaying an event never produces a
duplicate write.
"""

import base64
import binascii
import hashlib
import mimetypes
import os
import tempfile
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import httpx

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.workspace.base import BaseWorkspace


logger = get_logger(__name__)

# Directory (relative to the workspace working dir) where materialized content
# is written. Kept out of the way of normal project files.
MATERIALIZE_SUBDIR = ".materialized"

# Default cap for http(s) downloads (bytes). Guards against unbounded fetches.
DEFAULT_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB


def parse_data_url(url: str) -> tuple[bytes, str | None]:
    """Decode a ``data:<mime>;base64,<payload>`` URL into ``(bytes, mime_type)``.

    Only base64-encoded data URLs are supported. Raises ``ValueError`` on any
    malformed input.
    """
    if not url.startswith("data:"):
        raise ValueError("Not a data: URL")
    header, sep, encoded = url[len("data:") :].partition(",")
    if not sep:
        raise ValueError("Malformed data URL: missing comma separator")
    if ";base64" not in header:
        raise ValueError("Only base64-encoded data URLs are supported")
    mime_type = header.split(";", 1)[0] or None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"Invalid base64 payload in data URL: {e}") from e
    return raw, mime_type


def download_url(
    url: str, *, max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
) -> tuple[bytes, str | None]:
    """Download an ``http(s)://`` URL, enforcing a size cap.

    The cap is checked both against the ``Content-Length`` header (when present)
    and against the number of bytes actually streamed, so a lying or missing
    header cannot bypass it. Raises ``ValueError`` if the cap is exceeded.
    """
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) > max_bytes:
                raise ValueError(
                    f"Refusing to download {url}: Content-Length {declared} "
                    f"exceeds cap of {max_bytes} bytes"
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"Refusing to download {url}: stream exceeded cap of "
                        f"{max_bytes} bytes"
                    )
                chunks.append(chunk)
            mime_type = response.headers.get("content-type")
            if mime_type:
                mime_type = mime_type.split(";", 1)[0].strip() or None
    return b"".join(chunks), mime_type


def _extension_for(mime_type: str | None) -> str:
    if not mime_type:
        return ".bin"
    return mimetypes.guess_extension(mime_type) or ".bin"


def write_bytes_to_workspace(
    workspace: "BaseWorkspace",
    data: bytes,
    *,
    mime_type: str | None,
) -> tuple[str, int]:
    """Write ``data`` into the workspace under a content-addressed path.

    Returns ``(absolute_path, size_bytes)``. The filename is derived from the
    SHA-256 of the bytes, so repeated calls with identical content resolve to
    the same destination and never write duplicates.
    """
    digest = hashlib.sha256(data).hexdigest()[:16]
    filename = f"{digest}{_extension_for(mime_type)}"
    rel_path = str(PurePosixPath(MATERIALIZE_SUBDIR) / filename)
    dest_path = str(PurePosixPath(workspace.working_dir) / rel_path)

    # Idempotency: if the content-addressed file already exists, skip the write.
    if _workspace_file_exists(workspace, dest_path):
        logger.debug("Materialized file already exists, skipping write: %s", dest_path)
        return dest_path, len(data)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = workspace.file_upload(tmp_path, dest_path)
        if not result.success:
            raise RuntimeError(f"Failed to write materialized file: {result.error}")
    finally:
        os.unlink(tmp_path)
    return dest_path, len(data)


def _workspace_file_exists(workspace: "BaseWorkspace", path: str) -> bool:
    """Best-effort check whether ``path`` already exists in the workspace."""
    try:
        result = workspace.execute_command(f"test -f {_shquote(path)}")
        return result.exit_code == 0
    except Exception:
        return False


def _shquote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"
