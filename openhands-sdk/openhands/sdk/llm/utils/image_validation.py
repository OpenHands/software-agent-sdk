"""Drop image content the provider is guaranteed to reject.

A ``data:`` URL whose base64 payload does not decode makes the provider fail
the whole request with a 400 that names the offending message offset. Because
the payload lives in a persisted event, every later turn rebuilds the same
request and fails identically. The rollback recovery introduced with
``LLMHistoryContentRejectedError`` catches that on the way back, but pays a
turn to do it — the corrupted screenshot is discarded and retaken.

This pass runs on the way out and drops just the undecodable image, letting
the rest of the turn's work survive. Prevention is the fast path; the
rollback in ``LocalConversation`` is the safety net for anything this misses.

See https://github.com/OpenHands/software-agent-sdk/issues/5224 for the
upstream masker bug that produces such payloads today, and
https://github.com/OpenHands/software-agent-sdk/issues/5225 for the
conversation-wedge symptom the rollback recovery addresses.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

from openhands.sdk.llm.message import ImageContent, Message, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

UNDECODABLE_IMAGE_PLACEHOLDER = (
    "[Image omitted: its data was corrupted and the model provider would "
    "reject it. The original event is preserved in the conversation history.]"
)

# Bound the cache so a long-running process cannot grow it without limit.
# Long-lived conversations reuse the same image URLs across turns; the cache
# keeps that from re-decoding each turn. URLs whose decode returned False are
# also cached — a corrupt image tends to stay corrupt.
_DECODE_CACHE_SIZE = 256


@lru_cache(maxsize=_DECODE_CACHE_SIZE)
def _is_decodable(url: str) -> bool:
    """Return True unless ``url`` is a ``data:`` URL with undecodable base64.

    Anything that isn't a ``data:...;base64,...`` URL — plain http(s), a
    percent-encoded data URL — bails out immediately and is treated as the
    provider's problem to validate.
    """
    _header, separator, encoded = url.partition(";base64,")
    if not separator:
        return True
    try:
        # Encoders may wrap long payloads at 76 characters, so newlines
        # inside the payload are legal. Strip them before validating.
        base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def drop_undecodable_images(messages: list[Message]) -> list[Message]:
    """Return ``messages`` with undecodable images swapped for a placeholder.

    Returns the input list unchanged (same object) when every image decodes,
    which is the common case. When an image is dropped it is replaced with a
    ``TextContent`` placeholder so the model still sees that *something* was
    attached — silently deleting it would leave the agent's model of the
    world out of sync with what the provider actually received.
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
            dropped = len(item.image_urls) - len(decodable)
            logger.warning(
                "Dropping %d undecodable image(s) from an outgoing LLM "
                "message; the provider would otherwise reject the request "
                "and the current turn would have to be rolled back.",
                dropped,
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
