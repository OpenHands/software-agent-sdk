"""Optional preview for command output that would be middle-cut.

Unset SUPERCOMPRESS_API_KEY and nothing here runs. The head/tail cut in
maybe_truncate stays the fallback. Each distinct oversized output is sent at
most once per process, and each events_to_messages call makes at most one
new request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from openhands.sdk.utils.truncate import _save_full_content, maybe_truncate


logger = logging.getLogger(__name__)

SUPERCOMPRESS_URL = "https://api.supercompress.dev/v1/compress"
MAX_CONTEXT_CHARS = 120_000
MAX_QUERY_CHARS = 4_000
REQUEST_TIMEOUT_S = 5.0

_query: ContextVar[str | None] = ContextVar(
    "openhands_supercompress_query", default=None
)
_budget: ContextVar[int] = ContextVar("openhands_supercompress_budget", default=0)
_cache: dict[str, str | None] = {}


@contextmanager
def supercompress_query(query: str | None) -> Iterator[None]:
    """Allow at most one new compress request while this block runs."""
    text = query.strip()[:MAX_QUERY_CHARS] if query and query.strip() else None
    query_token = _query.set(text)
    budget_token = _budget.set(1 if text else 0)
    try:
        yield
    finally:
        _query.reset(query_token)
        _budget.reset(budget_token)


def prepare_command_output(
    content: str,
    *,
    truncate_after: int | None,
    save_dir: str | None = None,
    tool_prefix: str = "terminal",
    opener=None,
) -> str:
    """Middle-cut ``content``, unless one compress call returns a shorter preview."""
    compressed = _maybe_compress(content, truncate_after, opener=opener)
    if compressed is None:
        return maybe_truncate(
            content,
            truncate_after=truncate_after,
            save_dir=save_dir,
            tool_prefix=tool_prefix,
        )

    shown = compressed
    if save_dir:
        saved = _save_full_content(content, save_dir, tool_prefix)
        if saved:
            shown = f"{compressed}\n[Full output: {saved}]"
    if truncate_after and len(shown) <= truncate_after:
        logger.debug(
            "supercompress chars %s -> %s", len(content), len(compressed)
        )
        return shown
    return maybe_truncate(
        shown,
        truncate_after=truncate_after,
        save_dir=None,
        tool_prefix=tool_prefix,
    )


def _maybe_compress(
    content: str,
    truncate_after: int | None,
    *,
    opener=None,
) -> str | None:
    if not truncate_after or truncate_after < 0 or len(content) <= truncate_after:
        return None
    if len(content) > MAX_CONTEXT_CHARS:
        return None
    query = _query.get()
    api_key = os.environ.get("SUPERCOMPRESS_API_KEY", "").strip()
    if not query or not api_key:
        return None

    cache_key = hashlib.sha256(
        f"{hashlib.sha256(api_key.encode()).hexdigest()}\n{query}\n{content}".encode()
    ).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]
    if _budget.get() <= 0:
        return None
    _budget.set(_budget.get() - 1)
    compressed = compress_context(content, query, api_key, opener=opener)
    _cache[cache_key] = compressed
    return compressed


def compress_context(
    context: str,
    query: str,
    api_key: str,
    *,
    opener=None,
    timeout: float = REQUEST_TIMEOUT_S,
) -> str | None:
    """POST {context, query}. Return compressed_text only when it is shorter."""
    key = api_key.strip()
    query = query.strip()[:MAX_QUERY_CHARS]
    if not key or not query or not context or len(context) > MAX_CONTEXT_CHARS:
        return None
    payload = json.dumps({"context": context, "query": query}).encode()
    request = urllib.request.Request(
        SUPERCOMPRESS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method="POST",
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        logger.warning("supercompress skipped status=%s", exc.code)
        return None
    except Exception as exc:
        logger.warning("supercompress skipped: %s", type(exc).__name__)
        return None
    try:
        body = json.loads(raw.decode())
    except (UnicodeError, json.JSONDecodeError):
        return None
    text = body.get("compressed_text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text or len(text) >= len(context):
        return None
    return text


def clear_supercompress_cache() -> None:
    """Drop cached previews. Tests use this."""
    _cache.clear()
