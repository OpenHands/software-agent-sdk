from __future__ import annotations

import base64
import copy
import io

from PIL import Image

from openhands.sdk.llm.message import ImageContent, Message
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Anthropic vision docs: requests with more than 20 images cap each image at
# 2000x2000 pixels. Requests with 20 or fewer images cap each image at
# 8000x8000 pixels.
# https://docs.anthropic.com/en/docs/build-with-claude/vision
ANTHROPIC_MANY_IMAGE_THRESHOLD = 20
ANTHROPIC_MANY_IMAGE_MAX_DIMENSION = 2000
ANTHROPIC_STANDARD_IMAGE_MAX_DIMENSION = 8000


def maybe_resize_messages_for_provider(
    messages: list[Message], *, provider: str | None, vision_enabled: bool
) -> list[Message]:
    """Return a detached message list with provider-specific image resizing."""
    max_dimension = _get_image_max_dimension(
        messages=messages,
        provider=provider,
        vision_enabled=vision_enabled,
    )
    if max_dimension is None:
        return messages

    resized_messages = copy.deepcopy(messages)
    for message in resized_messages:
        for content_item in message.content:
            if isinstance(content_item, ImageContent):
                content_item.image_urls = [
                    _resize_base64_data_url(url, max_dimension=max_dimension)
                    for url in content_item.image_urls
                ]
    return resized_messages


def _get_image_max_dimension(
    messages: list[Message], *, provider: str | None, vision_enabled: bool
) -> int | None:
    if not vision_enabled or provider != "anthropic":
        return None

    total_images = sum(
        len(content_item.image_urls)
        for message in messages
        for content_item in message.content
        if isinstance(content_item, ImageContent)
    )
    if total_images == 0:
        return None
    if total_images <= ANTHROPIC_MANY_IMAGE_THRESHOLD:
        return ANTHROPIC_STANDARD_IMAGE_MAX_DIMENSION

    return ANTHROPIC_MANY_IMAGE_MAX_DIMENSION


def _resize_base64_data_url(url: str, *, max_dimension: int) -> str:
    if not url.startswith("data:image/"):
        return url

    header, sep, encoded = url.partition(";base64,")
    if not sep:
        return url

    mime_type = header.removeprefix("data:")

    try:
        raw_bytes = base64.b64decode(encoded)
        with Image.open(io.BytesIO(raw_bytes)) as image:
            if max(image.size) <= max_dimension:
                return url

            image.thumbnail(
                (max_dimension, max_dimension),
                Image.Resampling.LANCZOS,
            )
            image_format = image.format or mime_type.split("/", 1)[1].upper()

            if image_format == "JPG":
                image_format = "JPEG"

            output_image = image
            if image_format == "JPEG" and image.mode not in ("RGB", "L"):
                output_image = image.convert("RGB")

            buffer = io.BytesIO()
            output_image.save(buffer, format=image_format)
    except Exception:
        logger.warning(
            "Failed to resize base64 data image for outgoing LLM request",
            exc_info=True,
        )
        return url

    resized_encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime_type};base64,{resized_encoded}"
