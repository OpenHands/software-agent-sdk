"""HTTP and WebSocket forwarding to authenticated conversation containers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import websockets
from fastapi import HTTPException, status
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from openhands.sdk.logger import get_logger


class ProxyTarget(Protocol):
    host: str
    api_key: str | None


logger = get_logger(__name__)

# Hop-by-hop headers (RFC 7230) — must not be forwarded by a proxy.
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "authorization",
        "x-session-api-key",
        "cookie",
        "set-cookie",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        # ``host`` and ``content-length`` are recomputed by httpx; forwarding
        # the original values causes spurious 400s when bodies are re-chunked.
        "host",
        "content-length",
    }
)

# Stream chunk size for request/response bodies. 64 KiB is the same default
# httpx uses internally; we pin it so behavior is stable across versions.
_CHUNK_SIZE = 64 * 1024


def _filter_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


def strip_auth_query(path: str) -> str:
    parsed = urlsplit(path)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower()
            not in {"session_api_key", "x-session-api-key", "authorization"}
        ]
    )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
    )


async def proxy_http(
    request: Request,
    workspace: ProxyTarget,
    *,
    upstream_path: str,
    timeout: float | None = None,
) -> StreamingResponse:
    """Forward ``request`` to the per-conversation container.

    Args:
        request: Incoming Starlette request on the outer agent-server.
        workspace: The proxy target for the target container.
        upstream_path: Path (including any query string) on the inner
            agent-server to forward to. Typically the same path the outer
            server received, since the inner agent-server exposes the same
            API surface.
        timeout: Per-request timeout in seconds. ``None`` (the default) means
            no read timeout — conversation event streams can be long-lived.

    Notes:
        A fresh :class:`httpx.AsyncClient` is created per request. We avoid a
        long-lived pool because the outer server can serve many concurrent
        conversations and each one talks to a different upstream port — and
        because making the client per-request keeps the lifespan/teardown
        story trivial.
    """
    if request.headers.get("x-expose-secrets"):
        raise HTTPException(422, "Runtime secret exposure is unsupported")
    url = workspace.host + strip_auth_query(upstream_path)
    headers = _filter_headers(request.headers)
    # If the client authenticated via the workspace-session cookie (used by
    # iframe / img embeds that can't attach custom headers), there's no
    # ``X-Session-API-Key`` on the inbound request — but the inner
    # agent-server only knows about the header. Synthesize one from the
    # workspace's stored key so the inner accepts the proxied request.
    if workspace.api_key:
        headers["X-Session-API-Key"] = workspace.api_key

    async def _request_body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            if chunk:
                yield chunk

    # Keep HTTPX's contexts open until StreamingResponse consumes the body.
    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0)
            )
        )
        upstream = await stack.enter_async_context(
            client.stream(request.method, url, headers=headers, content=_request_body())
        )
    except BaseException as exc:
        await stack.aclose()
        if not isinstance(exc, httpx.HTTPError):
            raise
        logger.warning("Conversation upstream connection failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Conversation container unreachable",
        ) from exc

    async def _response_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw(chunk_size=_CHUNK_SIZE):
                yield chunk
        finally:
            await stack.aclose()

    return StreamingResponse(
        _response_body(),
        status_code=upstream.status_code,
        headers=_filter_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


async def bridge_websocket(
    client_ws: WebSocket,
    workspace: ProxyTarget,
    *,
    upstream_path: str,
) -> None:
    """Bridge a WebSocket session between the browser and an inner container.

    Precondition: ``client_ws`` MUST already be accepted by the caller. The
    bridge does not call ``accept()`` itself because the outer server's
    WebSocket-auth helper accepts on success and calling ``accept()`` a
    second time would raise.

    Closure semantics: when either side closes (or errors), we close the
    other side and return. No reconnect.
    """
    upstream_url = workspace.host.replace("http://", "ws://").replace(
        "https://", "wss://"
    ) + strip_auth_query(upstream_path)

    # The local sockets router accepts auth via header / query param / first
    # message. By the time we get here the outer has already accepted the
    # socket — but the inner is a separate server that requires its own
    # auth. Mint the inner-side ``X-Session-API-Key`` from the workspace's
    # shared key. (If both outer and inner have no key requirement, the
    # workspace.api_key is None and we send no header — that's fine.)
    upstream_headers: dict[str, str] = {}
    if workspace.api_key:
        upstream_headers["X-Session-API-Key"] = workspace.api_key

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=upstream_headers or None,
        ) as upstream_ws:
            await _bridge_websocket_loop(client_ws, upstream_ws)
    except websockets.exceptions.InvalidStatus as exc:
        logger.warning("Upstream WebSocket rejected (%s) to %s", exc, workspace.host)
        # 1011 == "internal error"; closest match for an upstream HTTP failure
        # since browsers can't see HTTP status codes from a failed upgrade.
        await client_ws.close(code=1011)
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        logger.warning(
            "Upstream WebSocket connect failed to %s: %s", workspace.host, exc
        )
        await client_ws.close(code=1011)


async def _bridge_websocket_loop(client_ws: WebSocket, upstream_ws) -> None:
    async def _client_to_upstream() -> None:
        try:
            while True:
                message = await client_ws.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if "bytes" in message and message["bytes"] is not None:
                    await upstream_ws.send(message["bytes"])
                elif "text" in message and message["text"] is not None:
                    await upstream_ws.send(message["text"])
        except WebSocketDisconnect:
            return

    async def _upstream_to_client() -> None:
        try:
            async for message in upstream_ws:
                if isinstance(message, (bytes, bytearray)):
                    await client_ws.send_bytes(bytes(message))
                else:
                    await client_ws.send_text(message)
        except websockets.exceptions.ConnectionClosed:
            return

    tasks = {
        asyncio.create_task(_client_to_upstream()),
        asyncio.create_task(_upstream_to_client()),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await upstream_ws.close()
        except Exception as exc:
            logger.debug("Upstream WebSocket close failed (%s)", type(exc).__name__)
        try:
            await client_ws.close()
        except Exception as exc:
            logger.debug("Client WebSocket close failed (%s)", type(exc).__name__)
