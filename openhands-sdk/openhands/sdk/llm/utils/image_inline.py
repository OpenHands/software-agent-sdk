"""Inline ``http(s)://`` image URLs as base64 ``data:`` URLs.

Some model APIs (notably Moonshot's public Kimi endpoint) reject http(s)
image URLs and only accept base64-encoded image content. When this pass is
active, every ``ImageContent`` whose entry is not already a ``data:`` URL is
fetched and rewritten as ``data:{mime};base64,{...}`` before the request
leaves the SDK.

Failures are non-fatal: the original URL is preserved and the upstream is
allowed to produce its native error. We also keep a small in-memory cache so
the same image is not re-downloaded on every conversation turn.

Security: requests are validated against an SSRF block-list of loopback,
private, link-local, multicast and otherwise reserved IP ranges, and
redirects are followed manually so each hop is revalidated. Set
``OH_INLINE_IMAGE_ALLOW_PRIVATE_HOSTS=1`` only in tests/dev to bypass.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import ipaddress
import os
import socket
from collections import OrderedDict
from threading import Lock
from urllib.parse import urljoin, urlparse

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

# Per-image fetch timeout (seconds). Override via env for slow networks.
DEFAULT_FETCH_TIMEOUT_S = 30.0
FETCH_TIMEOUT_S: float = float(
    os.environ.get("OH_INLINE_IMAGE_FETCH_TIMEOUT_S", DEFAULT_FETCH_TIMEOUT_S)
)

# Maximum number of HTTP redirects we will follow manually.
MAX_REDIRECTS = 5

# Opt-out switch for the SSRF check; intended for tests/dev only.
_ALLOW_PRIVATE_HOSTS = os.environ.get(
    "OH_INLINE_IMAGE_ALLOW_PRIVATE_HOSTS", ""
).lower() in {"1", "true", "yes"}

_EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "tif": "image/tiff",
}

# Only inline content the upstream model can actually decode as an image.
# If the server returns ``200 OK`` with ``text/html`` (soft-404, auth wall,
# CDN error page), inlining the bytes would produce ``data:text/html;...``
# which the model silently rejects. We fall back to the original URL instead.
_ALLOWED_IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
    }
)


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
            logger.debug(
                "Image too large to cache (%d bytes > %d byte budget); "
                "will re-fetch. url=%s",
                encoded_size,
                self._max_bytes,
                url,
            )
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
    fast path that returns the input list unchanged. Otherwise, fetches and
    inlines URL-formatted images using a single shared ``httpx.Client`` so
    multi-image turns benefit from connection pooling.
    """
    if not vision_enabled or not inline_required:
        return messages

    out: list[Message] | None = None
    client: httpx.Client | None = None
    try:
        for msg_index, message in enumerate(messages):
            new_content_items: list | None = None
            for item_index, item in enumerate(message.content):
                if not isinstance(item, ImageContent):
                    continue
                new_urls: list[str] = []
                changed = False
                for url in item.image_urls:
                    # Only construct the client when we know we need to make
                    # a real network call. URLs already served from the cache
                    # (e.g. on second turns) must not trigger a client setup.
                    if _needs_fetch(url) and _CACHE.get(url) is None and client is None:
                        client = httpx.Client(
                            timeout=FETCH_TIMEOUT_S,
                            follow_redirects=False,
                        )
                    inlined = _inline_url(url, client)
                    new_urls.append(inlined)
                    if inlined != url:
                        changed = True
                if not changed:
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
    finally:
        if client is not None:
            client.close()

    return out if out is not None else messages


async def amaybe_inline_image_urls(
    messages: list[Message],
    *,
    inline_required: bool,
    vision_enabled: bool,
) -> list[Message]:
    """Async variant: offload the blocking fetch+encode work to a worker thread.

    Used by ``acompletion`` / ``aresponses`` so the event loop is not blocked
    while images download.
    """
    if not vision_enabled or not inline_required:
        return messages
    return await asyncio.to_thread(
        maybe_inline_image_urls,
        messages,
        inline_required=inline_required,
        vision_enabled=vision_enabled,
    )


def _needs_fetch(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _inline_url(url: str, client: httpx.Client | None) -> str:
    """Return the URL unchanged or a ``data:`` URL with base64 image bytes."""
    if url.startswith("data:"):
        return url
    if not _needs_fetch(url):
        # Unknown scheme (e.g. ``ms://<file_id>``): leave it to the upstream.
        return url

    cached = _CACHE.get(url)
    if cached is not None:
        return cached

    # ``assert`` would be stripped under ``python -O``; use an explicit raise
    # so the contract is enforced regardless of the optimisation level. The
    # invariant is maintained by ``maybe_inline_image_urls``, so this path is
    # unreachable in practice.
    if client is None:
        raise RuntimeError(
            "internal error: httpx.Client must be provided for http(s) URLs"
        )

    try:
        data_url = _fetch_and_encode(url, client)
    except Exception as e:
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


def _fetch_and_encode(url: str, client: httpx.Client) -> str:
    """Fetch ``url`` (with manual, SSRF-validated redirect following) and
    return the resulting ``data:`` URL."""
    max_bytes = MAX_IMAGE_DOWNLOAD_MB * 1024 * 1024
    current_url = url
    # +1: the initial fetch does not count against MAX_REDIRECTS, so this
    # loop performs 1 original request + up to MAX_REDIRECTS redirect hops.
    for _ in range(MAX_REDIRECTS + 1):
        _validate_url_target(current_url)
        with client.stream("GET", current_url) as response:
            if response.is_redirect:
                location = response.headers.get("Location") or response.headers.get(
                    "location"
                )
                if not location:
                    # ``raise_for_status()`` is a no-op on 3xx, so raise
                    # directly to surface a clear, logged error.
                    raise ValueError(f"Redirect without Location header: {current_url}")
                current_url = urljoin(current_url, location)
                continue
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

    raise ValueError(f"Exceeded max {MAX_REDIRECTS} redirects for url={url}")


def _validate_url_target(url: str) -> None:
    """Raise ``ValueError`` if ``url`` resolves to a non-public address.

    Blocks loopback, private, link-local, multicast, broadcast and reserved
    ranges (both IPv4 and IPv6), plus syntactic shortcuts like ``localhost``
    and ``*.local`` mDNS hostnames. Called on the initial URL and on every
    redirect target.

    Note (TOCTOU): httpx performs a second DNS resolution at connect time,
    so a rogue short-TTL DNS server could serve a public IP during this
    check and a private IP on the real connection. This is a known
    limitation of the validate-then-connect pattern and is considered
    acceptable for this threat model — inlining is bounded by size, MIME
    type and timeout, and failures fall back to the original URL.
    """
    if _ALLOW_PRIVATE_HOSTS:
        return

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Refusing non-http(s) scheme {scheme!r}: {url}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no host: {url}")

    host_lower = host.lower().rstrip(".")
    if host_lower in {"localhost", "ip6-localhost", "ip6-loopback"} or (
        host_lower.endswith(".localhost") or host_lower.endswith(".local")
    ):
        raise ValueError(f"Refusing to fetch local/mDNS host: {host}")

    # IP literal? Validate directly without DNS.
    try:
        ip = ipaddress.ip_address(host_lower.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None:
        if _is_disallowed_ip(ip):
            raise ValueError(f"Refusing to fetch disallowed IP {ip}: {url}")
        return

    # Hostname: resolve and check every returned address.
    port = parsed.port or (443 if scheme == "https" else 80)
    addrs = _resolve_host_ips(host, port)
    if not addrs:
        raise ValueError(f"DNS returned no addresses for host {host}: {url}")
    for ip_str in addrs:
        try:
            resolved = ipaddress.ip_address(ip_str)
        except ValueError:
            # Skip anything we can't interpret as an IP literal.
            continue
        if _is_disallowed_ip(resolved):
            raise ValueError(
                f"Refusing to fetch host {host} resolving to disallowed "
                f"address {resolved}: {url}"
            )


def _resolve_host_ips(host: str, port: int) -> list[str]:
    """Return the unique IP literals that ``host`` resolves to.

    Extracted as a module-level function so tests can patch it without
    poisoning the in-process DNS cache or requiring real network access.
    """
    try:
        addrinfo = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for host {host}: {e}") from e
    seen: list[str] = []
    for entry in addrinfo:
        sockaddr = entry[4]
        # sockaddr is (host, port) for IPv4 and (host, port, flowinfo, scopeid)
        # for IPv6; the host element is always the IP literal as a string.
        ip_str = sockaddr[0]
        if isinstance(ip_str, str) and ip_str not in seen:
            seen.append(ip_str)
    return seen


def _is_disallowed_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or (
            isinstance(ip, ipaddress.IPv4Address)
            and ip == ipaddress.IPv4Address("255.255.255.255")
        )
    )


def _derive_mime_type(content_type_header: str | None, url: str) -> str:
    """Return a validated image MIME type for the response body.

    If the server reports a non-image ``Content-Type`` (e.g. ``text/html``
    from a soft-404 or auth wall), raise ``ValueError`` so the caller can
    fall back to the original URL instead of inlining unusable bytes.
    """
    mime = ""
    if content_type_header:
        mime = content_type_header.split(";", 1)[0].strip()
    if not mime:
        mime = _mime_from_url(url)
    if mime not in _ALLOWED_IMAGE_MIMES:
        raise ValueError(f"Unexpected Content-Type {mime!r} for image URL: {url}")
    return mime


def _mime_from_url(url: str) -> str:
    """Best-effort MIME inference from the URL path extension.

    Returns ``""`` when the extension is unknown or absent so that
    ``_derive_mime_type`` raises and the caller falls back to the original
    URL. We deliberately do not guess ``image/png`` here: a soft-404 or
    auth-wall that omits ``Content-Type`` would otherwise sneak past the
    ``_ALLOWED_IMAGE_MIMES`` guard.
    """
    path = url.split("?", 1)[0].split("#", 1)[0]
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_TO_MIME.get(ext, "")
