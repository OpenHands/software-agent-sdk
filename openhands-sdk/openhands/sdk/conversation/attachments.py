"""Save images a user attaches to a message as files the agent's tools can use."""

import base64
import binascii
import hashlib
import mimetypes
from pathlib import Path

from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def _decode_data_url(url: str) -> tuple[bytes, str]:
    header, sep, encoded = url.removeprefix("data:").partition(";base64,")
    if not sep or not header:
        raise ValueError("not a base64 data URL")
    try:
        return base64.b64decode(encoded, validate=True), header
    except binascii.Error as e:
        raise ValueError(f"invalid base64 payload: {e}") from e


def _write_attachment(data: bytes, mime_type: str, target_dir: Path) -> Path:
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    path = target_dir / f"{hashlib.sha256(data).hexdigest()[:16]}{extension}"
    if not path.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path.resolve()


def save_message_images(message: Message, target_dir: Path) -> TextContent | None:
    """Write the message's inline images to ``target_dir`` and describe them.

    Only ``data:`` URLs are saved; remote image URLs are left to the agent.
    Returns text listing the saved paths (and any image that could not be
    saved), or ``None`` when the message carries no inline images.
    """
    lines: list[str] = []
    for content in message.content:
        if not isinstance(content, ImageContent):
            continue
        for url in content.image_urls:
            if not url.startswith("data:"):
                continue
            try:
                data, mime_type = _decode_data_url(url)
                path = _write_attachment(data, mime_type, target_dir)
            except (ValueError, OSError) as e:
                logger.warning("Could not save attached image: %s", e)
                lines.append("- (one attached image could not be saved to a file)")
                continue
            lines.append(f"- {path} ({mime_type}, {len(data)} bytes)")

    if not lines:
        return None
    header = (
        "The images attached to this message are also saved as files. "
        "Use these paths when a task needs the image itself as a file "
        "(for example, uploading it as an attachment):"
    )
    return TextContent(text="\n".join([header, *lines]))
