"""Cloud proxy router.

Forwards browser-originated requests to a configured cloud SaaS host so the
GUI never has to make a cross-origin request. The browser talks to this
local agent-server (same-origin in production, allowlisted localhost in
dev) and this server makes the upstream call server-side, where CORS does
not apply.

Hosts are allowlisted to prevent the proxy from being abused as an SSRF
relay. By default only `*.all-hands.dev` is permitted; the operator can
override via the ``OH_CLOUD_PROXY_ALLOWED_HOSTS`` environment variable
(comma-separated list of hostnames or suffixes).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

cloud_proxy_router = APIRouter(prefix="/cloud-proxy", tags=["Cloud Proxy"])

_DEFAULT_ALLOWED_HOSTS = ("all-hands.dev",)
_DENYLISTED_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class CloudProxyRequest(BaseModel):
    """Envelope describing the upstream request to forward."""

    host: str = Field(
        description=(
            "Cloud host base URL, e.g. 'https://app.all-hands.dev'. Must "
            "match the configured allowlist."
        )
    )
    method: str = Field(default="GET")
    path: str = Field(description="Path on the cloud host, e.g. '/api/organizations'")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Headers to forward, including the Authorization bearer token "
            "for the cloud backend."
        ),
    )
    body: Any = None
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


def _allowed_hosts() -> tuple[str, ...]:
    raw = os.environ.get("OH_CLOUD_PROXY_ALLOWED_HOSTS")
    if not raw:
        return _DEFAULT_ALLOWED_HOSTS
    parsed = tuple(entry.strip().lower() for entry in raw.split(",") if entry.strip())
    return parsed or _DEFAULT_ALLOWED_HOSTS


def _is_host_allowed(host_url: str) -> bool:
    parsed = urlparse(host_url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False
    if hostname in _DENYLISTED_HOSTNAMES:
        # Block loopback to prevent the proxy from being used to reach
        # other local services on the operator's machine.
        return False
    for entry in _allowed_hosts():
        entry_lower = entry.lower()
        if hostname == entry_lower or hostname.endswith("." + entry_lower):
            return True
    return False


# A small set of hop-by-hop / framing headers we should never forward.
_STRIPPED_RESPONSE_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
    # Don't leak upstream CORS state into the local response — irrelevant
    # to the local-origin caller and confusing if it disagrees.
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-headers",
    "access-control-allow-methods",
    "access-control-expose-headers",
    "access-control-max-age",
    # Don't propagate Set-Cookie into a different origin/agent-server.
    "set-cookie",
}


def _filtered_response_headers(upstream: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _STRIPPED_RESPONSE_HEADERS
    }


async def _forward_upstream(
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: Any,
    raw_body: bytes | None,
    timeout_seconds: float,
) -> httpx.Response:
    """Make the upstream HTTP call.

    Factored out so tests can mock it without touching the test harness's
    own httpx clients.
    """
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        return await client.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body,
            content=raw_body,
        )


@cloud_proxy_router.post("")
async def cloud_proxy(req: CloudProxyRequest) -> Response:
    if not _is_host_allowed(req.host):
        raise HTTPException(
            status_code=403,
            detail=f"Cloud proxy host not allowed: {req.host}",
        )

    upstream_url = f"{req.host.rstrip('/')}{req.path}"

    # httpx supports passing dict/list as `json=` and bytes/str as `content=`.
    json_body: Any = None
    raw_body: bytes | None = None
    if isinstance(req.body, (dict, list)):
        json_body = req.body
    elif isinstance(req.body, str):
        raw_body = req.body.encode("utf-8")
    elif req.body is not None:
        # Coerce anything else through JSON so the upstream sees consistent
        # content. Avoids accidental tuple/None ambiguity.
        json_body = req.body

    try:
        upstream = await _forward_upstream(
            method=req.method.upper(),
            url=upstream_url,
            headers=req.headers,
            json_body=json_body,
            raw_body=raw_body,
            timeout_seconds=req.timeout_seconds,
        )
    except httpx.RequestError as exc:
        logger.warning("Cloud proxy upstream error for %s: %s", upstream_url, exc)
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc

    media_type = upstream.headers.get("content-type", "application/octet-stream")
    headers = _filtered_response_headers(upstream)

    if "application/json" in media_type:
        try:
            payload = upstream.json()
        except ValueError:
            # Upstream lied about its content-type. Fall through to bytes.
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=media_type,
                headers=headers,
            )
        return JSONResponse(
            content=payload,
            status_code=upstream.status_code,
            headers=headers,
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=media_type,
        headers=headers,
    )
