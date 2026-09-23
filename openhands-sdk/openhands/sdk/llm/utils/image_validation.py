"""Drop image content the provider is guaranteed to reject.

A ``data:`` URL whose base64 payload does not decode makes the provider fail
the whole request with a 400 that names the offending message offset. Because
the payload lives in a persisted event, every later turn rebuilds the same
request and fails identically, so one corrupt image wedges the conversation
(https://github.com/OpenHands/software-agent-sdk/issues/5225).

Replacing the undecodable image with a text placeholder on the way out keeps
the rest of the turn intact and leaves the stored event untouched for audit.
"""

from __future__ import annotations

import base64
import binascii

from openhands.sdk.llm.message import ImageContent, Message, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

UNDECODABLE_IMAGE_PLACEHOLDER = (
    "[Image omitted: its data was corrupted and the model provider would "
    "reject it. The original event is preserved in the conversation history.]"
)


def _is_decodable(url: str) -> bool:
    """Return True unless ``url`` is a ``data:`` URL with undecodable base64."""
    _header, separator, encoded = url.partition(";base64,")
    if not separator:
        # An http(s) URL is resolved by the provider, not by us.
        return True
    try:
        # Encoders may wrap long payloads, so newlines are not corruption;
        # anything else outside the alphabet is.
        base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def drop_undecodable_images(messages: list[Message]) -> list[Message]:
    """Return ``messages`` with undecodable images swapped for a placeholder.

    The input list is returned unchanged when every image decodes, which is
    the overwhelmingly common case.
    """
    out: list[Message] = []
    changed = False

    for message in messages:
        content: list[TextContent | ImageContent] = []
        message_changed = False

        for item in message.content:
            if not isinstance(item, ImageContent):
                content.append(item)
                continue

            decodable = [url for url in item.image_urls if _is_decodable(url)]
            if len(decodable) == len(item.image_urls):
                content.append(item)
                continue

            message_changed = True
            logger.warning(
                "Dropping %d undecodable image(s) from an outgoing LLM message; "
                "the provider would reject the request.",
                len(item.image_urls) - len(decodable),
            )
            if decodable:
                content.append(item.model_copy(update={"image_urls": decodable}))
            content.append(TextContent(text=UNDECODABLE_IMAGE_PLACEHOLDER))

        if message_changed:
            changed = True
            out.append(message.model_copy(update={"content": content}))
        else:
            out.append(message)

    return out if changed else messages
