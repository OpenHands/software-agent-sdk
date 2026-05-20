"""Inline ``http(s)://`` image URLs as base64 ``data:`` URLs.

Some model APIs (notably Moonshot's public Kimi endpoint) reject http(s)
image URLs and only accept base64-encoded image content. When this pass is
active, every ``ImageContent`` whose entry is not already a ``data:`` URL is
fetched and rewritten as ``data:{mime};base64,{...}`` before the request
leaves the SDK.

Failures are non-fatal: the original URL is preserved and the upstream is
allowed to produce its native error. We also keep a small in-memory cache so
the same image is not re-downloaded on every conversation turn.
"""

from __future__ import annotations

import base64
import copy
import os
from collections import OrderedDict
from threading import Lock

import httpx

from openhands.sdk.llm.message import ImageContent, Message
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Max individual image size we are willing to download, in megabytes.
# Mirrors LiteLLM's MAX_IMAGE_URL_DOWNLOAD_SIZE_MB default.
DEFAULT_MAX_IMAGE_DOWNLOAD_MB = 20
MAX_IMAGE_DOWNLOAD_MB: int = int(
    os.environ.get("OH_INLINE_IMAGE_MAX_MB", DEFAULT_MAX_IMAGE_DOWNLOAD_MB)
)

# Cap how much memory the in-process cache may hold across all inlined images.
DEFAULT_CACHE_MAX_BYTES = 64 * 1024 * 1024  # 64 MB
CACHE_MAX_BYTES: int = int(
    os.environ.get("OH_INLINE_IMAGE_CACHE_BYTES", DEFAULT_CACHE_MAX_BYTES)
)

# Per-request fetch timeout.
DEFAULT_FETCH_TIMEOUT_S = 30.0

_EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


class _DataUrlCache:
    """Bounded LRU cache mapping URL → ``data:`` URL.

    Size is bounded by the total encoded size of cached entries so a few
    very large images can't push everything else out.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._size_bytes = 0
        self._lock = Lock()

    def get(self, url: str) -> str | None:
        with self._lock:
            value = self._entries.get(url)
            if value is not None:
                self._entries.move_to_end(url)
            return value

    def put(self, url: str, data_url: str) -> None:
        encoded_size = len(data_url)
        if encoded_size > self._max_bytes:
            # A single image larger than the cache budget: skip caching it.
            return
        with self._lock:
            existing = self._entries.pop(url, None)
            if existing is not None:
                self._size_bytes -= len(existing)
            self._entries[url] = data_url
            self._size_bytes += encoded_size
            while self._size_bytes > self._max_bytes and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._size_bytes -= len(evicted)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size_bytes = 0


_CACHE = _DataUrlCache(max_bytes=CACHE_MAX_BYTES)


def maybe_inline_image_urls(
    messages: list[Message],
    *,
    inline_required: bool,
    vision_enabled: bool,
) -> list[Message]:
    """Return a detached message list with http(s) image URLs inlined as base64.

    When ``inline_required`` or ``vision_enabled`` is False this is a no-op
    fast path that returns the input list unchanged.
    """
    if not vision_enabled or not inline_required:
        return messages

    out: list[Message] | None = None
    for msg_index, message in enumerate(messages):
        new_content_items: list | None = None
        for item_index, item in enumerate(message.content):
            if not isinstance(item, ImageContent):
                continue
            new_urls = [_inline_url(u) for u in item.image_urls]
            if new_urls == item.image_urls:
                continue
            if new_content_items is None:
                new_content_items = list(message.content)
            new_content_items[item_index] = item.model_copy(
                update={"image_urls": new_urls}
            )
        if new_content_items is None:
            continue
        if out is None:
            out = copy.copy(messages)
        out[msg_index] = message.model_copy(update={"content": new_content_items})

    return out if out is not None else messages


def _inline_url(url: str) -> str:
    """Return the URL unchanged or a ``data:`` URL with base64 image bytes."""
    if url.startswith("data:"):
        return url
    if not (url.startswith("http://") or url.startswith("https://")):
        # Unknown scheme (e.g. ``ms://<file_id>``): leave it to the upstream.
        return url

    cached = _CACHE.get(url)
    if cached is not None:
        return cached

    try:
        data_url = _fetch_and_encode(url)
    except Exception as e:  # pragma: no cover - best-effort fallback
        logger.warning(
            "Failed to inline image URL as base64; sending original URL. "
            "url=%s error=%s: %s",
            url,
            type(e).__name__,
            e,
        )
        return url

    _CACHE.put(url, data_url)
    return data_url


def _fetch_and_encode(url: str) -> str:
    max_bytes = MAX_IMAGE_DOWNLOAD_MB * 1024 * 1024
    with httpx.Client(follow_redirects=True, timeout=DEFAULT_FETCH_TIMEOUT_S) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                size_mb = int(content_length) / (1024 * 1024)
                raise ValueError(
                    f"Image exceeds {MAX_IMAGE_DOWNLOAD_MB}MB cap "
                    f"({size_mb:.2f}MB). url={url}"
                )
            mime_type = _derive_mime_type(response.headers.get("Content-Type"), url)
            buffer = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    size_mb = len(buffer) / (1024 * 1024)
                    raise ValueError(
                        f"Image exceeds {MAX_IMAGE_DOWNLOAD_MB}MB cap "
                        f"({size_mb:.2f}MB). url={url}"
                    )

    encoded = base64.b64encode(bytes(buffer)).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _derive_mime_type(content_type_header: str | None, url: str) -> str:
    if content_type_header:
        return content_type_header.split(";", 1)[0].strip() or _mime_from_url(url)
    return _mime_from_url(url)


def _mime_from_url(url: str) -> str:
    path = url.split("?", 1)[0].split("#", 1)[0]
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_TO_MIME.get(ext, "image/png")
