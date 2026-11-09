"""Expose selected conversation REST operations through MCP."""

import re
from contextvars import ContextVar

import httpx2
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.utilities.openapi import HttpMethod
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openhands.agent_server.config import Config


_session_api_key: ContextVar[str | None] = ContextVar(
    "mcp_session_api_key", default=None
)
_REST_TOOLS: tuple[tuple[str, HttpMethod, str], ...] = (
    ("/api/conversations", "POST", "start_conversation"),
    ("/api/conversations/{conversation_id}", "GET", "get_conversation"),
    ("/api/conversations/{conversation_id}/events", "POST", "send_message"),
)


class LocalSessionAuth:
    """Match the REST API's optional X-Session-API-Key requirement."""

    def __init__(self, app: ASGIApp, parent: FastAPI):
        self.app = app
        self.parent = parent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        init = getattr(self.parent.state, "init_service", None)
        if init is not None and init.state != "ready":
            response = JSONResponse({"detail": "Server is initializing"}, 503)
            await response(scope, receive, send)
            return
        config: Config = self.parent.state.config
        key = Headers(scope=scope).get("x-session-api-key")
        if config.session_api_keys and key not in config.session_api_keys:
            response = JSONResponse({"detail": "Unauthorized"}, 401)
            await response(scope, receive, send)
            return
        token = _session_api_key.set(key)
        try:
            await self.app(scope, receive, send)
        finally:
            _session_api_key.reset(token)


def create_conversation_mcp(parent: FastAPI) -> tuple[FastMCP, ASGIApp]:
    """Convert only the chosen REST operations, retaining their schemas and behavior."""
    paths = parent.openapi()["paths"]
    names = {
        paths[path][method.lower()]["operationId"]: name
        for path, method, name in _REST_TOOLS
    }
    route_maps = [
        RouteMap(
            methods=[method],
            pattern=rf"^{re.escape(path)}$",
            mcp_type=MCPType.TOOL,
        )
        for path, method, _ in _REST_TOOLS
    ]
    route_maps.append(RouteMap(mcp_type=MCPType.EXCLUDE))

    async def forward_session_key(request: httpx2.Request) -> None:
        if key := _session_api_key.get():
            request.headers["X-Session-API-Key"] = key

    mcp = FastMCP.from_fastapi(
        app=parent,
        name="OpenHands",
        route_maps=route_maps,
        mcp_names=names,
        httpx_client_kwargs={"event_hooks": {"request": [forward_session_key]}},
        mask_error_details=True,
    )
    app = mcp.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        middleware=[Middleware(LocalSessionAuth, parent=parent)],
    )
    return mcp, app
