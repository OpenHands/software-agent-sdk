"""Authenticated proxy for browser requests to OpenHands runtime hosts.

Cloud Canvas cannot call a per-conversation runtime directly from the browser:
the runtime host is not the same origin and may not expose CORS headers. The
Canvas client therefore sends a request envelope to this agent-server, which
forwards it to the runtime after validating the destination.

This endpoint is intentionally limited to OpenHands-managed runtime hosts. A
generic authenticated URL fetcher would be an SSRF primitive because session
API keys are accepted by this server.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, JsonValue, field_validator


ALLOWED_RUNTIME_HOST_SUFFIXES = (
    ".prod-runtime.all-hands.dev",
    ".staging-runtime.all-hands.dev",
)

_REQUEST_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "content-encoding",
        "host",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

_RESPONSE_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class CloudProxyRequest(BaseModel):
    """Request envelope for forwarding a browser call to a runtime host."""

    host: str = Field(description="Absolute upstream OpenHands runtime host")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(description="Upstream absolute path, including query string")
    headers: dict[str, str] = Field(default_factory=dict)
    body: JsonValue | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        parsed = urlparse(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("host must not include an invalid port") from exc

        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("host must be an absolute https URL without a port")

        hostname = parsed.hostname.lower().rstrip(".")
        if not hostname.endswith(ALLOWED_RUNTIME_HOST_SUFFIXES):
            raise ValueError("host is not an allowed OpenHands runtime host")

        return f"https://{hostname}"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        ):
            raise ValueError("path must be an absolute path")
        return value


cloud_proxy_router = APIRouter(prefix="/cloud-proxy", tags=["Cloud Proxy"])


def _ensure_public_dns_target(host: str) -> None:
    parsed = urlparse(host)
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="host is required")

    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=400,
            detail="host could not be resolved",
        ) from exc

    if not addresses:
        raise HTTPException(status_code=400, detail="host could not be resolved")

    for *_, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="host resolves to a non-public address",
            )


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep end-to-end headers while letting httpx own transport headers."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _REQUEST_HOP_BY_HOP_HEADERS
    }


@cloud_proxy_router.post(
    "",
    response_class=Response,
    responses={200: {"description": "Upstream response"}},
)
async def proxy_cloud_request(request: CloudProxyRequest) -> Response:
    """Forward an authenticated request to an allowed OpenHands runtime."""
    await asyncio.to_thread(_ensure_public_dns_target, request.host)
    url = f"{request.host}{request.path}"
    timeout = request.timeout_seconds or 30.0

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            upstream = await client.request(
                request.method,
                url,
                headers=_forward_headers(request.headers),
                json=request.body if request.body is not None else None,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="upstream request failed") from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _RESPONSE_HOP_BY_HOP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
