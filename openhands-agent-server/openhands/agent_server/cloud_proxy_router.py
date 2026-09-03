from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator


class CloudProxyRequest(BaseModel):
    """Request envelope for proxying browser calls to cloud/runtime hosts."""

    host: str = Field(description="Absolute upstream host, for example https://x")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(description="Upstream absolute path, including query string")
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("host must be an absolute http(s) URL")
        return f"{parsed.scheme}://{parsed.netloc}"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("path must start with /")
        return value


cloud_proxy_router = APIRouter(prefix="/cloud-proxy", tags=["Cloud Proxy"])


@cloud_proxy_router.post("")
async def proxy_cloud_request(request: CloudProxyRequest) -> Response:
    """Proxy a cloud/runtime request that cannot be made directly by the browser."""
    url = urljoin(f"{request.host}/", request.path.lstrip("/"))
    timeout = request.timeout_seconds or 30.0

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                url,
                headers=request.headers,
                json=request.body if request.body is not None else None,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    excluded_headers = {
        "content-encoding",
        "content-length",
        "connection",
        "transfer-encoding",
    }
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_headers
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )
